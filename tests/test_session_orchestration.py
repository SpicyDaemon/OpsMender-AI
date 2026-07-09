"""v2 Phase 2 durable session-capacity orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    ApprovalRequest,
    Base,
    Incident,
    IncidentPage,
    ModelConfig,
    Organization,
    Service,
    Session,
)
from backend.db.repos import (
    ApprovalRequestRepo,
    AuditEntryRepo,
    IncidentAssignmentRepo,
    SessionRepo,
)
from backend.services.session_orchestration import (
    admit_session,
    drain_session_queue,
    sweep_approval_holds,
)


ORG_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="Queue Test", slug="queue-test"))
        await db.commit()
    yield maker
    await engine.dispose()


async def _seed_model_service(db, *, cap: int = 1):
    model = ModelConfig(
        org_id=ORG_ID,
        name=f"queue-model-{uuid.uuid4().hex[:8]}",
        provider="ollama",
        model_id=f"queue-model-{uuid.uuid4().hex[:8]}",
        max_concurrent_sessions=cap,
        is_default=True,
    )
    db.add(model)
    await db.flush()
    service = Service(
        org_id=ORG_ID,
        team_id=uuid.uuid4(),
        name=f"Queue service {uuid.uuid4().hex[:6]}",
        slug=f"queue-service-{uuid.uuid4().hex[:8]}",
        priority="P2",
        model_config_ids=[str(model.id)],
    )
    db.add(service)
    await db.flush()
    return model, service


async def _incident(db, service: Service, *, priority: str, title: str):
    incident = Incident(
        org_id=ORG_ID,
        title=title,
        description="capacity orchestration test",
        priority=priority,
        service_id=service.id,
    )
    db.add(incident)
    await db.flush()
    return incident


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


async def test_priority_then_fifo_drain_and_paging_independence(
    factory, monkeypatch
):
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

    async with factory() as db:
        model, service = await _seed_model_service(db)
        occupying = await SessionRepo.create(
            db,
            ORG_ID,
            tier=2,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        p1_first = await _incident(db, service, priority="P1", title="P1 first")
        p1_second = await _incident(db, service, priority="P1", title="P1 second")
        p0_later = await _incident(db, service, priority="P0", title="P0 later")
        responder_id = uuid.uuid4()
        page = IncidentPage(
            org_id=ORG_ID,
            incident_id=p0_later.id,
            user_id=responder_id,
            channel="recorded",
            delivery_status="recorded",
        )
        db.add(page)
        first = await admit_session(
            db, ORG_ID, incident=p1_first, tier=0, queue_ttl_seconds=900
        )
        second = await admit_session(
            db, ORG_ID, incident=p1_second, tier=0, queue_ttl_seconds=900
        )
        urgent = await admit_session(
            db, ORG_ID, incident=p0_later, tier=0, queue_ttl_seconds=900
        )
        assert first.queued and second.queued and urgent.queued
        await SessionRepo.set_status(
            db,
            ORG_ID,
            occupying.id,
            status="completed",
            ended_at=datetime.now(timezone.utc),
        )
        await db.commit()

    app = _app(factory)
    assert await drain_session_queue(app, org_id=ORG_ID) == 1
    assert dispatched == [urgent.session.id]

    async with factory() as db:
        urgent_row = await SessionRepo.get_by_id(db, ORG_ID, urgent.session.id)
        first_row = await SessionRepo.get_by_id(db, ORG_ID, first.session.id)
        second_row = await SessionRepo.get_by_id(db, ORG_ID, second.session.id)
        assert urgent_row is not None and urgent_row.status == "active"
        assert first_row is not None and first_row.status == "queued"
        assert second_row is not None and second_row.status == "queued"
        # Admission and drain never cancel or delay the human paging trail.
        persisted_page = await db.get(IncidentPage, page.id)
        assert persisted_page is not None
        assert persisted_page.user_id == responder_id
        await SessionRepo.set_status(
            db,
            ORG_ID,
            urgent.session.id,
            status="completed",
            ended_at=datetime.now(timezone.utc),
        )
        await db.commit()

    assert await drain_session_queue(app, org_id=ORG_ID) == 1
    assert dispatched[-1] == first.session.id


async def test_queue_ttl_and_handled_incident_are_cancelled(factory, monkeypatch):
    monkeypatch.setattr(
        "backend.services.session_orchestration.schedule_session_chat_event",
        lambda *args, **kwargs: None,
    )
    async with factory() as db:
        model, service = await _seed_model_service(db)
        await SessionRepo.create(
            db,
            ORG_ID,
            tier=2,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        expired_incident = await _incident(
            db, service, priority="P0", title="Expired queue wait"
        )
        handled_incident = await _incident(
            db, service, priority="P1", title="Handled while queued"
        )
        expired = await admit_session(
            db, ORG_ID, incident=expired_incident, tier=0, queue_ttl_seconds=900
        )
        handled = await admit_session(
            db, ORG_ID, incident=handled_incident, tier=0, queue_ttl_seconds=900
        )
        expired.session.queue_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        handled_incident.status = "resolved"
        await db.commit()

    assert await drain_session_queue(_app(factory), org_id=ORG_ID) == 0
    async with factory() as db:
        expired_row = await SessionRepo.get_by_id(db, ORG_ID, expired.session.id)
        handled_row = await SessionRepo.get_by_id(db, ORG_ID, handled.session.id)
        assert expired_row is not None and expired_row.status == "cancelled"
        assert "expired" in (expired_row.summary or "").lower()
        assert handled_row is not None and handled_row.status == "cancelled"
        assert "handled" in (handled_row.summary or "").lower()


async def test_any_human_assignment_cancels_a_queued_session(factory):
    async with factory() as db:
        model, service = await _seed_model_service(db)
        await SessionRepo.create(
            db,
            ORG_ID,
            tier=2,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        incident = await _incident(
            db, service, priority="P0", title="Ack cancels queue"
        )
        queued = await admit_session(db, ORG_ID, incident=incident, tier=0)
        await IncidentAssignmentRepo.assign(
            db,
            ORG_ID,
            incident_id=incident.id,
            user_id=uuid.uuid4(),
            assigned_by="test_ack",
        )
        await db.commit()

    async with factory() as db:
        cancelled = await SessionRepo.get_by_id(db, ORG_ID, queued.session.id)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert "acknowledged" in (cancelled.summary or "").lower()


async def test_force_starts_queued_session_and_is_audited(factory):
    actor_id = uuid.uuid4()
    async with factory() as db:
        model, service = await _seed_model_service(db)
        await SessionRepo.create(
            db,
            ORG_ID,
            tier=2,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        incident = await _incident(db, service, priority="P0", title="Force start")
        queued = await admit_session(
            db, ORG_ID, incident=incident, tier=0, queue_ttl_seconds=900
        )
        forced = await admit_session(
            db,
            ORG_ID,
            incident=incident,
            tier=0,
            force=True,
            actor_user_id=actor_id,
        )
        assert forced.session.id == queued.session.id
        assert forced.start_required is True
        assert forced.warning is not None and "1/1" in forced.warning
        await db.commit()

    async with factory() as db:
        session = await SessionRepo.get_by_id(db, ORG_ID, queued.session.id)
        assert session is not None
        assert session.status == "active"
        assert session.force_started is True
        assert session.force_started_by == actor_id
        occupancy = await SessionRepo.active_occupancy_for_model_config(
            db, ORG_ID, model.id
        )
        assert occupancy == 2
        entries = await AuditEntryRepo.list_by_session(db, ORG_ID, session.id)
        force_entry = next(
            entry for entry in entries if entry.entry_type == "session_force_start"
        )
        assert force_entry.result["incident_id"] == str(incident.id)
        assert force_entry.result["occupancy"] == 1
        assert force_entry.result["cap"] == 1
        assert force_entry.result["from_queue"] is True


async def test_manual_takeover_reuses_the_active_session(factory):
    async with factory() as db:
        model, service = await _seed_model_service(db)
        incident = await _incident(db, service, priority="P0", title="Take over")
        existing = await SessionRepo.create(
            db,
            ORG_ID,
            tier=0,
            incident_id=incident.id,
            model_config_id=model.id,
            model_provider=model.provider,
            model_id=model.model_id,
        )
        admission = await admit_session(
            db,
            ORG_ID,
            incident=incident,
            tier=1,
            takeover_existing=True,
        )
        assert admission.session.id == existing.id
        assert admission.takeover is True
        assert admission.start_required is True
        sessions = await SessionRepo.list_by_incident(db, ORG_ID, incident.id)
        assert [session.id for session in sessions] == [existing.id]


async def test_approval_hold_warns_extends_and_expires(factory, monkeypatch):
    monkeypatch.setattr(
        "backend.services.session_orchestration.schedule_queue_drain",
        lambda *args, **kwargs: None,
    )
    now = datetime.now(timezone.utc)
    async with factory() as db:
        session = Session(org_id=ORG_ID, tier=1, status="awaiting_approval")
        db.add(session)
        await db.flush()
        approval = ApprovalRequest(
            org_id=ORG_ID,
            session_id=session.id,
            action={"tool": "restart_service"},
            expires_at=now + timedelta(seconds=30),
        )
        db.add(approval)
        await db.commit()

    app = _app(factory)
    assert await sweep_approval_holds(app) == 1
    async with factory() as db:
        warned = await ApprovalRequestRepo.get_by_id(db, ORG_ID, approval.id)
        assert warned is not None and warned.extension_notified_at is not None
        extended_expiry = now + timedelta(minutes=15)
        assert await ApprovalRequestRepo.extend(
            db,
            ORG_ID,
            approval.id,
            expires_at=extended_expiry,
        )
        await db.commit()
        extended = await ApprovalRequestRepo.get_by_id(db, ORG_ID, approval.id)
        assert extended is not None
        assert extended.extension_count == 1
        assert extended.extension_notified_at is None
        extended.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    assert await sweep_approval_holds(app) == 0
    async with factory() as db:
        expired = await ApprovalRequestRepo.get_by_id(db, ORG_ID, approval.id)
        timed_out = await SessionRepo.get_by_id(db, ORG_ID, session.id)
        assert expired is not None and expired.status == "expired"
        assert timed_out is not None and timed_out.status == "timed_out"
        assert timed_out.ended_at is not None
