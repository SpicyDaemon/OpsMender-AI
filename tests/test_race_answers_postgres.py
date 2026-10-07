"""M1-41 (R-03, R-04) on real PostgreSQL connections: identical alerts that
arrive together all get a normal answer and open one incident, and two Takes
together on an incident without a chain leave one owner and give the other
the usual 409. Run with PART4_PG_URL pointed at a disposable database. The
URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.auth import hash_password
from backend.api.deps import set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import (
    AlertFingerprintState,
    Base,
    Incident,
    IncidentAssignment,
    Organization,
)
from backend.db.repos import IncidentRepo, ServiceRepo, TeamRepo, UserRepo

pytestmark = pytest.mark.integration

_PASSWORD = "race-check-password"
ROUNDS = 8


@pytest.fixture
async def pg_app(tmp_path):
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url, pool_size=10)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    org_id = uuid.uuid4()
    tag = org_id.hex[:8]
    async with factory() as db:
        db.add(Organization(id=org_id, name="Race check", slug=f"race-{tag}"))
        await db.flush()
        names = {}
        for role, who in (
            ("admin", "admin"),
            ("operator", "first"),
            ("operator", "second"),
        ):
            user = await UserRepo.create(
                db,
                username=f"race-{tag}-{who}",
                email=f"race-{tag}-{who}@example.test",
                password_hash=hash_password(_PASSWORD),
                role=role,
                primary_org_id=org_id,
            )
            await UserRepo.add_to_organization(
                db, user_id=user.id, org_id=org_id, role=role
            )
            names[who] = user
        team = await TeamRepo.create(
            db, org_id, name=f"Orders {tag}", slug=f"orders-{tag}"
        )
        for who in ("first", "second"):
            await TeamRepo.add_member(db, org_id, team.id, user_id=names[who].id)
        # Alert grouping on, so identical alerts also race for the noise state.
        service = await ServiceRepo.create(
            db,
            org_id,
            team_id=team.id,
            name=f"Checkout {tag}",
            slug=f"checkout-{tag}",
            alert_grouping="on",
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
        headers = {}
        for who, user in names.items():
            login = await client.post(
                "/auth/login", json={"username": user.username, "password": _PASSWORD}
            )
            assert login.status_code == 200, login.status_code
            headers[who] = {"Authorization": f"Bearer {login.json()['access_token']}"}
        yield type(
            "PG",
            (),
            {
                "client": client,
                "factory": factory,
                "org_id": org_id,
                "service_id": service.id,
                "team_id": team.id,
                "headers": headers,
            },
        )
    set_env_path(None)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _count(factory, model, *criteria) -> int:
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*criteria))


async def _grouped_intake(pg_app, round_no: int) -> str:
    """A fresh service with alert grouping on, so each round's alerts race
    for its noise state and don't fold into an earlier round's incident."""
    async with pg_app.factory() as db:
        service = await ServiceRepo.create(
            db,
            pg_app.org_id,
            team_id=pg_app.team_id,
            name=f"Round {round_no} {uuid.uuid4().hex[:6]}",
            slug=f"round-{round_no}-{uuid.uuid4().hex[:6]}",
            alert_grouping="on",
        )
        await db.commit()
    rotated = await pg_app.client.post(
        f"/services/{service.id}/intake-url", headers=pg_app.headers["admin"]
    )
    assert rotated.status_code == 200, rotated.text
    return rotated.json()["intake_url"]


async def test_identical_alerts_together_get_answers_and_one_incident(pg_app):
    for round_no in range(ROUNDS):
        intake = await _grouped_intake(pg_app, round_no)
        alert = {
            "title": f"disk full {round_no}",
            "severity": "high",
            "id": f"disk-{round_no}",
        }
        answers = await asyncio.gather(
            *(pg_app.client.post(intake, json=alert) for _ in range(4))
        )
        statuses = sorted(resp.status_code for resp in answers)
        assert statuses == [200, 200, 200, 200], (round_no, statuses)
        actions = sorted(resp.json()["dedup_action"] for resp in answers)
        assert actions.count("created") == 1, (round_no, actions)
        incident_ids = {resp.json()["incident_id"] for resp in answers}
        assert len(incident_ids) == 1, (round_no, incident_ids)
        assert (
            await _count(
                pg_app.factory,
                Incident,
                Incident.org_id == pg_app.org_id,
                Incident.external_id == f"disk-{round_no}",
            )
            == 1
        )
    # One noise state per fingerprint, however the alerts raced.
    async with pg_app.factory() as db:
        duplicates = await db.scalar(
            text(
                "SELECT count(*) FROM (SELECT fingerprint FROM alert_fingerprint_states "
                "WHERE org_id = :org GROUP BY service_id, fingerprint HAVING count(*) > 1) d"
            ),
            {"org": pg_app.org_id},
        )
    assert duplicates == 0
    assert (
        await _count(
            pg_app.factory,
            AlertFingerprintState,
            AlertFingerprintState.org_id == pg_app.org_id,
        )
        >= 1
    )


async def test_two_takes_together_leave_one_owner(pg_app):
    for round_no in range(ROUNDS):
        async with pg_app.factory() as db:
            incident = await IncidentRepo.create(
                db,
                pg_app.org_id,
                title=f"no chain {round_no}",
                description="M1-41 check",
                service_id=pg_app.service_id,
            )
            await db.commit()
        takes = await asyncio.gather(
            *(
                pg_app.client.post(
                    f"/incidents/{incident.id}/take",
                    json={},
                    headers=pg_app.headers[who],
                )
                for who in ("first", "second")
            )
        )
        statuses = sorted(resp.status_code for resp in takes)
        assert statuses == [200, 409], (round_no, statuses, [r.text for r in takes])
        assert (
            await _count(
                pg_app.factory,
                IncidentAssignment,
                IncidentAssignment.incident_id == incident.id,
                IncidentAssignment.released_at.is_(None),
            )
            == 1
        )
