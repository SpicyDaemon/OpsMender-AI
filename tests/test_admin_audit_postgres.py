"""Configuration changes and their Activity entries commit together.

Run with PART4_PG_URL pointed at a disposable database. The URL is not logged.
A trigger makes PostgreSQL reject audit rows, so these tests show what the
route returns and what stays committed when the audit write fails.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.auth import hash_password
from backend.api.deps import set_session_factory
from backend.audit.admin_changes import set_audit_actor
from backend.config_loader import set_env_path
from backend.db.models import (
    AuditEntry,
    Base,
    Incident,
    IngestToken,
    Organization,
    PasswordResetToken,
    Session,
)
from backend.db.repos import (
    AuditEntryRepo,
    IncidentRepo,
    SessionRepo,
    TeamRepo,
    UserRepo,
)

pytestmark = pytest.mark.integration

_PASSWORD = "audit-check-password"


@pytest.fixture
async def pg_app(tmp_path):
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    org_id = uuid.uuid4()
    suffix = org_id.hex[:8]
    async with factory() as db:
        db.add(Organization(id=org_id, name="Audit check", slug=f"audit-{suffix}"))
        await db.flush()
        admin = await UserRepo.create(
            db,
            username=f"admin-{suffix}",
            email=f"admin-{suffix}@example.test",
            password_hash=hash_password(_PASSWORD),
            role="admin",
            primary_org_id=org_id,
        )
        target = await UserRepo.create(
            db,
            username=f"target-{suffix}",
            email=f"target-{suffix}@example.test",
            password_hash="unused",
            role="viewer",
            primary_org_id=org_id,
        )
        await db.commit()
    env = tmp_path / ".env"
    env.write_text(
        "OPSMENDER_TIER=2\n"
        f"OPSMENDER_AUDIT_LOG={tmp_path / 'audit.jsonl'}\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(env)
    app = create_app()
    app.state.engine = engine
    app.state.session_factory = factory
    # Report a server error as the client would see it instead of raising.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/auth/login", json={"username": admin.username, "password": _PASSWORD}
        )
        assert login.status_code == 200, login.status_code
        yield SimpleNamespace(
            client=client,
            engine=engine,
            factory=factory,
            org_id=org_id,
            admin_id=admin.id,
            target_id=target.id,
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
    set_env_path(None)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.execute(text("DROP FUNCTION IF EXISTS audit_check_reject()"))
    await engine.dispose()


async def _reject_audit_rows(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION audit_check_reject() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'audit row rejected by test'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER audit_check_reject BEFORE INSERT ON audit_entries "
                "FOR EACH ROW EXECUTE FUNCTION audit_check_reject()"
            )
        )


async def _count(factory, model, *criteria) -> int:
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*criteria))


async def _entries(factory, org_id, route):
    async with factory() as db:
        rows = await db.execute(
            select(AuditEntry).where(
                AuditEntry.org_id == org_id,
                AuditEntry.entry_type == "admin_change",
                AuditEntry.tool_name.like(f"% {route}"),
            )
        )
        return list(rows.scalars())


async def test_reset_link_commits_with_one_entry(pg_app):
    route = "/auth/users/{user_id}/reset-password"
    response = await pg_app.client.post(
        f"/auth/users/{pg_app.target_id}/reset-password", headers=pg_app.headers
    )

    assert response.status_code == 200, response.status_code
    assert (
        await _count(
            pg_app.factory,
            PasswordResetToken,
            PasswordResetToken.user_id == pg_app.target_id,
        )
        == 1
    )
    entries = await _entries(pg_app.factory, pg_app.org_id, route)
    assert len(entries) == 1
    assert entries[0].tool_parameters == {
        "actor_id": str(pg_app.admin_id),
        "route": route,
        "method": "POST",
        "entity": "users",
        "entity_id": str(pg_app.target_id),
        "operation": "created",
    }
    stored = str([(entry.tool_parameters, entry.result) for entry in entries])
    link_stored = response.json()["url"] in stored
    assert not link_stored


async def test_rejected_entry_rolls_back_reset_link(pg_app):
    await _reject_audit_rows(pg_app.engine)

    response = await pg_app.client.post(
        f"/auth/users/{pg_app.target_id}/reset-password", headers=pg_app.headers
    )

    observed = (
        response.status_code,
        await _count(
            pg_app.factory,
            PasswordResetToken,
            PasswordResetToken.user_id == pg_app.target_id,
        ),
        await _count(pg_app.factory, AuditEntry),
    )
    # (status, reset links, audit rows): an error, and nothing committed.
    assert observed == (500, 0, 0)


async def test_ingest_token_commits_with_one_entry(pg_app):
    response = await pg_app.client.post(
        "/ingest-tokens",
        headers=pg_app.headers,
        json={"name": "audit-check", "provider": "generic"},
    )

    assert response.status_code == 201, response.status_code
    assert await _count(pg_app.factory, IngestToken) == 1
    entries = await _entries(pg_app.factory, pg_app.org_id, "/ingest-tokens")
    assert len(entries) == 1
    assert entries[0].tool_parameters["entity"] == "ingest_tokens"
    assert entries[0].tool_parameters["entity_id"] == response.json()["id"]
    stored = str([(entry.tool_parameters, entry.result) for entry in entries])
    token_stored = response.json()["token"] in stored
    assert not token_stored


async def test_rejected_entry_rolls_back_ingest_token(pg_app):
    await _reject_audit_rows(pg_app.engine)

    response = await pg_app.client.post(
        "/ingest-tokens",
        headers=pg_app.headers,
        json={"name": "audit-check", "provider": "generic"},
    )

    observed = (
        response.status_code,
        await _count(pg_app.factory, IngestToken),
        await _count(pg_app.factory, AuditEntry),
    )
    # A 201 here would hand out a token that was never saved.
    assert observed == (500, 0, 0)


async def test_rolled_back_savepoint_leaves_no_entry(pg_app):
    request = SimpleNamespace(
        scope={"route": SimpleNamespace(path="/teams")},
        url=SimpleNamespace(path="/teams"),
        method="POST",
        path_params={},
    )
    async with pg_app.factory() as db:
        set_audit_actor(db, request, actor_id=pg_app.admin_id, org_id=pg_app.org_id)
        kept = await TeamRepo.create(db, pg_app.org_id, name="Kept", slug="kept")
        with pytest.raises(RuntimeError):
            async with db.begin_nested():
                await TeamRepo.create(db, pg_app.org_id, name="Dropped", slug="dropped")
                raise RuntimeError("roll back the savepoint")
        async with db.begin_nested():
            also_kept = await TeamRepo.create(
                db, pg_app.org_id, name="Also kept", slug="also-kept"
            )
        await db.commit()

    entries = await _entries(pg_app.factory, pg_app.org_id, "/teams")
    assert sorted(entry.tool_parameters["entity_id"] for entry in entries) == sorted(
        [str(kept.id), str(also_kept.id)]
    )


async def _incident_with_session(pg_app):
    async with pg_app.factory() as db:
        incident = await IncidentRepo.create(
            db, pg_app.org_id, title="Deleted incident", description="M1-18 check"
        )
        session = await SessionRepo.create(
            db, pg_app.org_id, tier=1, incident_id=incident.id, status="completed"
        )
        for entry_type in ("session_start", "session_end"):
            await AuditEntryRepo.create(
                db,
                pg_app.org_id,
                session_id=session.id,
                tier=1,
                entry_type=entry_type,
                result={"ok": True},
            )
        await db.commit()
        return incident.id, session.id


async def test_incident_deletion_commits_with_one_entry(pg_app):
    incident_id, session_id = await _incident_with_session(pg_app)

    response = await pg_app.client.delete(
        f"/incidents/{incident_id}", headers=pg_app.headers
    )

    observed = (
        response.status_code,
        await _count(pg_app.factory, Incident, Incident.id == incident_id),
        await _count(pg_app.factory, Session, Session.id == session_id),
        await _count(
            pg_app.factory, AuditEntry, AuditEntry.entry_type == "incident_deleted"
        ),
        await _count(pg_app.factory, AuditEntry, AuditEntry.session_id.is_(None)),
    )
    # (status, incidents, sessions, deletion entries, detached entries)
    assert observed == (204, 0, 0, 1, 3)


async def test_rejected_entry_rolls_back_incident_deletion(pg_app):
    incident_id, session_id = await _incident_with_session(pg_app)
    await _reject_audit_rows(pg_app.engine)

    response = await pg_app.client.delete(
        f"/incidents/{incident_id}", headers=pg_app.headers
    )

    observed = (
        response.status_code,
        await _count(pg_app.factory, Incident, Incident.id == incident_id),
        await _count(pg_app.factory, Session, Session.id == session_id),
        await _count(pg_app.factory, AuditEntry, AuditEntry.session_id == session_id),
        await _count(
            pg_app.factory, AuditEntry, AuditEntry.entry_type == "incident_deleted"
        ),
    )
    # An error, and the incident, its session and attached entries all remain.
    assert observed == (500, 1, 1, 2, 0)


async def test_later_rejected_entry_rolls_back_bulk_incident_deletion(pg_app):
    first, first_session = await _incident_with_session(pg_app)
    second, second_session = await _incident_with_session(pg_app)
    async with pg_app.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION audit_check_reject() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.entry_type = 'incident_deleted' AND EXISTS ("
                "SELECT 1 FROM audit_entries a "
                "WHERE a.org_id = NEW.org_id AND a.entry_type = 'incident_deleted' "
                "AND NOT EXISTS (SELECT 1 FROM incidents i "
                "WHERE i.id::text = a.tool_parameters->>'incident_id')) THEN "
                "RAISE EXCEPTION 'audit row rejected by test'; END IF; "
                "RETURN NEW; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER audit_check_reject BEFORE INSERT ON audit_entries "
                "FOR EACH ROW EXECUTE FUNCTION audit_check_reject()"
            )
        )

    # Reject the later entry only after the first incident was really deleted
    # inside the transaction. A partial commit must not survive the HTTP error.
    response = await pg_app.client.post(
        "/incidents/bulk",
        json={"action": "delete", "incident_ids": [str(first), str(second)]},
        headers=pg_app.headers,
    )

    observed = (
        response.status_code,
        await _count(pg_app.factory, Incident, Incident.id.in_((first, second))),
        await _count(
            pg_app.factory, Session, Session.id.in_((first_session, second_session))
        ),
        await _count(
            pg_app.factory,
            AuditEntry,
            AuditEntry.session_id.in_((first_session, second_session)),
        ),
        await _count(
            pg_app.factory, AuditEntry, AuditEntry.entry_type == "incident_deleted"
        ),
        await _count(pg_app.factory, AuditEntry, AuditEntry.session_id.is_(None)),
    )
    assert observed == (500, 2, 2, 4, 0, 0)


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_concurrent_incident_deletion_records_each_incident_once(
    pg_app, monkeypatch, bulk
):
    from backend.api.routes import incidents as routes

    pairs = [await _incident_with_session(pg_app)]
    if bulk:
        pairs.append(await _incident_with_session(pg_app))
    incident_ids = [pair[0] for pair in pairs]
    session_ids = [pair[1] for pair in pairs]
    connections = {}
    reached_audit = set()
    first_ready = asyncio.Event()
    second_connected = asyncio.Event()
    release = asyncio.Event()
    original_get = IncidentRepo.get_by_id
    original_record = routes._record_incident_deletion

    async def tracked_get(db, *args, **kwargs):
        if id(db) not in connections:
            connections[id(db)] = await db.scalar(text("SELECT pg_backend_pid()"))
            if len(connections) == 2:
                second_connected.set()
        return await original_get(db, *args, **kwargs)

    async def paused_record(db, *args, **kwargs):
        reached_audit.add(id(db))
        first_ready.set()
        await release.wait()
        await original_record(db, *args, **kwargs)

    monkeypatch.setattr(IncidentRepo, "get_by_id", tracked_get)
    monkeypatch.setattr(routes, "_record_incident_deletion", paused_record)

    async def delete(ids):
        if bulk:
            return await pg_app.client.post(
                "/incidents/bulk",
                json={"action": "delete", "incident_ids": list(map(str, ids))},
                headers=pg_app.headers,
            )
        return await pg_app.client.delete(
            f"/incidents/{ids[0]}", headers=pg_app.headers
        )

    async def wait_for_overlap():
        while len(reached_audit) < 2:
            second_pid = list(connections.values())[1]
            async with pg_app.factory() as db:
                blocked = await db.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": second_pid},
                )
            if blocked:
                return
            await asyncio.sleep(0.01)

    first = asyncio.create_task(delete(incident_ids))
    second = None
    try:
        await asyncio.wait_for(first_ready.wait(), timeout=10)
        # Reversed bulk selection exercises consistent lock ordering, too.
        second = asyncio.create_task(delete(list(reversed(incident_ids))))
        await asyncio.wait_for(second_connected.wait(), timeout=10)
        await asyncio.wait_for(wait_for_overlap(), timeout=10)
        assert len(set(connections.values())) == 2
        release.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
    finally:
        release.set()
        tasks = [task for task in (first, second) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    statuses = sorted(response.status_code for response in responses)
    observed = (
        statuses,
        await _count(pg_app.factory, Incident, Incident.id.in_(incident_ids)),
        await _count(pg_app.factory, Session, Session.id.in_(session_ids)),
        await _count(
            pg_app.factory, AuditEntry, AuditEntry.entry_type == "incident_deleted"
        ),
        await _count(
            pg_app.factory,
            AuditEntry,
            AuditEntry.entry_type.in_(("session_start", "session_end")),
            AuditEntry.session_id.is_(None),
        ),
    )
    assert observed == ([200 if bulk else 204, 404], 0, 0, len(pairs), 2 * len(pairs))
    async with pg_app.factory() as db:
        entries = (
            await db.scalars(
                select(AuditEntry).where(AuditEntry.entry_type == "incident_deleted")
            )
        ).all()
        assert sorted(
            entry.tool_parameters["incident_id"] for entry in entries
        ) == sorted(map(str, incident_ids))
        assert all(entry.session_id is None and entry.timestamp for entry in entries)
