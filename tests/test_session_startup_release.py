"""Startup stops AI sessions a stopped process left running (EC-D05)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    ApprovalRequest,
    Base,
    Incident,
    IncidentComment,
    ModelConfig,
    Organization,
    Service,
    Session,
)
from backend.services.session_orchestration import (
    admit_session,
    drain_session_queue,
)

ORG_ID = uuid.UUID("10000000-0000-0000-0000-000000000023")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="Restart", slug="restart"))
        await db.commit()
    yield maker
    await engine.dispose()


def _app(factory):
    return SimpleNamespace(
        state=SimpleNamespace(
            session_factory=factory,
            background_tasks=set(),
            session_tasks=set(),
            config=SimpleNamespace(
                deployment=SimpleNamespace(mode="monolith", service_role="all"),
                sessions=SimpleNamespace(
                    approval_warning_seconds=60,
                    approval_hold_ttl_seconds=900,
                ),
            ),
        )
    )


async def _model(db, name):
    model = ModelConfig(
        org_id=ORG_ID,
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        provider="ollama",
        model_id=f"{name}-{uuid.uuid4().hex[:8]}",
        max_concurrent_sessions=1,
        is_default=name == "main",
    )
    db.add(model)
    await db.flush()
    service = Service(
        org_id=ORG_ID,
        team_id=uuid.uuid4(),
        name=f"Restart {name} {uuid.uuid4().hex[:6]}",
        slug=f"restart-{name}-{uuid.uuid4().hex[:8]}",
        priority="P2",
        model_config_ids=[str(model.id)],
    )
    db.add(service)
    await db.flush()
    return model, service


async def _incident(db, service, title):
    incident = Incident(
        org_id=ORG_ID,
        title=title,
        description="restart test",
        priority="P1",
        service_id=service.id,
    )
    db.add(incident)
    await db.flush()
    return incident


async def test_startup_stops_an_orphan_and_frees_its_slot(factory, monkeypatch):
    from backend.services.session_orchestration import (
        INTERRUPTED_SUMMARY,
        release_interrupted_sessions,
    )

    dispatched: list[uuid.UUID] = []

    async def capture_dispatch(_app, session_id):
        dispatched.append(session_id)

    monkeypatch.setattr(
        "backend.services.session_orchestration.dispatch_session_ready",
        capture_dispatch,
    )
    monkeypatch.setattr(
        "backend.services.session_orchestration.schedule_session_chat_event",
        lambda *args, **kwargs: None,
    )
    now = datetime.now(timezone.utc)
    async with factory() as db:
        model, service = await _model(db, "main")
        held_model, held_service = await _model(db, "held")
        cut_off = await _incident(db, service, "Cut off by the restart")
        waiting = await _incident(db, service, "Waiting for capacity")
        approval_incident = await _incident(db, held_service, "Waiting for approval")
        # The process died mid-run: still active, holding the only slot.
        orphan = Session(
            org_id=ORG_ID,
            tier=1,
            status="active",
            incident_id=cut_off.id,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        held = Session(
            org_id=ORG_ID,
            tier=1,
            status="awaiting_approval",
            incident_id=approval_incident.id,
            model_config_id=held_model.id,
            model_provider=held_model.provider,
            model_id=held_model.model_id,
        )
        db.add_all([orphan, held])
        await db.flush()
        approval = ApprovalRequest(
            org_id=ORG_ID,
            session_id=held.id,
            action={"tool": "restart_service"},
            expires_at=now + timedelta(minutes=10),
        )
        db.add(approval)
        queued = await admit_session(
            db, ORG_ID, incident=waiting, tier=0, queue_ttl_seconds=900
        )
        assert queued.queued
        await db.commit()

    app = _app(factory)
    # Before the release the orphan blocks the queue.
    assert await drain_session_queue(app, org_id=ORG_ID) == 0

    assert await release_interrupted_sessions(factory) == 1
    assert dispatched == []  # nothing is replayed or started
    # Running it again changes nothing and adds no second note.
    assert await release_interrupted_sessions(factory) == 0
    async with factory() as db:
        stopped = await db.get(Session, orphan.id)
        assert stopped.status == "stopped"
        assert stopped.summary == INTERRUPTED_SUMMARY
        assert stopped.ended_at is not None
        notes = (
            (
                await db.execute(
                    select(IncidentComment.body).where(
                        IncidentComment.incident_id == cut_off.id,
                        IncidentComment.source == "lifecycle",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert notes == [
            "The app restarted while an AI session was working on this incident, "
            "so the session was stopped. Nothing it was doing was retried."
        ]
        # Approval holds and queued work are left alone.
        assert (await db.get(Session, held.id)).status == "awaiting_approval"
        assert (await db.get(ApprovalRequest, approval.id)).status == "pending"
        assert (await db.get(Session, queued.session.id)).status == "queued"

    assert await drain_session_queue(app, org_id=ORG_ID) == 1
    assert dispatched == [queued.session.id]
    async with factory() as db:
        assert (await db.get(Session, queued.session.id)).status == "active"


async def test_an_orphans_pending_approval_expires_with_it(factory):
    from backend.services.session_orchestration import release_interrupted_sessions

    async with factory() as db:
        orphan = Session(org_id=ORG_ID, tier=1, status="active")
        db.add(orphan)
        await db.flush()
        approval = ApprovalRequest(
            org_id=ORG_ID,
            session_id=orphan.id,
            action={"tool": "restart_service"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add(approval)
        await db.commit()

    assert await release_interrupted_sessions(factory) == 1
    async with factory() as db:
        assert (await db.get(Session, orphan.id)).status == "stopped"
        assert (await db.get(ApprovalRequest, approval.id)).status == "expired"


@pytest.mark.parametrize(
    ("mode", "role", "released"),
    [("development", None, True), ("distributed", "scheduler", False)],
)
async def test_only_a_monolith_startup_releases_orphans(
    tmp_path, monkeypatch, mode, role, released
):
    """Distributed sessions run on workers that may still be up."""
    from backend.api import deps
    from backend.api.app import create_app
    from backend.config_loader import AppConfig

    url = f"sqlite+aiosqlite:///{(tmp_path / 'restart.db').as_posix()}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="Restart", slug="restart"))
        orphan = Session(org_id=ORG_ID, tier=1, status="active")
        db.add(orphan)
        await db.commit()

    monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", mode)
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "development")
    monkeypatch.setenv("OPSMENDER_DATABASE_URL", url)
    if role is None:
        monkeypatch.delenv("OPSMENDER_SERVICE_ROLE", raising=False)
    else:
        monkeypatch.setenv("OPSMENDER_SERVICE_ROLE", role)
    env_file = tmp_path / "restart.env"
    env_file.write_text(
        f"OPSMENDER_JWT_SECRET=restart-test-secret\nOPSMENDER_DATABASE_URL={url}\n",
        encoding="utf-8",
    )
    # The PostgreSQL event bus is not under test; SQLite stands in.
    monkeypatch.setattr(
        "backend.services.incident_events.IncidentEventPublisher",
        lambda database_url: SimpleNamespace(database_url=database_url),
    )
    # The lifespan installs a process-wide session factory; restore it after.
    monkeypatch.setattr(deps, "_session_factory", deps._session_factory)
    app = create_app(AppConfig.load(env_file))
    async with app.router.lifespan_context(app):
        pass

    async with maker() as db:
        row = await db.get(Session, orphan.id)
        assert row.status == ("stopped" if released else "active")
    await engine.dispose()
