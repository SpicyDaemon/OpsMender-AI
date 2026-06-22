"""In-app notification center (v1.2 — the bell): repo + emit service.

Covers:
- ``InAppNotificationRepo`` CRUD: create, list (incl. unread_only + paging),
  counts, mark read / mark-all-read, delete, and per-(org, user) scoping.
- ``emit_notification``: stores rows, honors per-category mute, and lets quiet
  hours suppress only the live push (the row is still stored).
- quiet-hours / mute helper edge cases (wrap-past-midnight, malformed values).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization, User
from backend.db.repos import InAppNotificationRepo, UserNotificationPrefRepo
from backend.notifications import CATEGORY_INCIDENT, emit_notification
from backend.notifications.service import _in_quiet_hours, _muted_categories

ORG = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
USER_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as db:
        db.add(Organization(id=ORG, name="Org", slug="org"))
        db.add(User(id=USER_A, username="a", email="a@x.com", password_hash="x"))
        db.add(User(id=USER_B, username="b", email="b@x.com", password_hash="x"))
        await db.commit()
    yield f
    await engine.dispose()


async def _make(db, user_id, **kw):
    kw.setdefault("event_type", "incident.assigned")
    kw.setdefault("category", CATEGORY_INCIDENT)
    kw.setdefault("title", "Assigned to you")
    return await InAppNotificationRepo.create(db, ORG, user_id, **kw)


# --- repo --------------------------------------------------------------------


async def test_create_and_list(factory):
    async with factory() as db:
        await _make(db, USER_A, title="one")
        await _make(db, USER_A, title="two")
        await db.commit()
    async with factory() as db:
        items = await InAppNotificationRepo.list_for_user(db, ORG, USER_A)
        assert [n.title for n in items] == ["two", "one"]  # newest first
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 2
        assert (
            await InAppNotificationRepo.count_for_user(
                db, ORG, USER_A, unread_only=True
            )
            == 2
        )


async def test_scoped_per_user(factory):
    async with factory() as db:
        await _make(db, USER_A)
        await _make(db, USER_B)
        await db.commit()
    async with factory() as db:
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 1
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_B) == 1
        # User B cannot fetch User A's notification.
        a_items = await InAppNotificationRepo.list_for_user(db, ORG, USER_A)
        assert (
            await InAppNotificationRepo.get_by_id(db, ORG, USER_B, a_items[0].id)
            is None
        )


async def test_mark_read_and_all(factory):
    async with factory() as db:
        n1 = await _make(db, USER_A)
        await _make(db, USER_A)
        await _make(db, USER_A)
        await db.commit()
        n1_id = n1.id
    async with factory() as db:
        assert await InAppNotificationRepo.mark_read(db, ORG, USER_A, n1_id) is True
        await db.commit()
    async with factory() as db:
        assert (
            await InAppNotificationRepo.count_for_user(
                db, ORG, USER_A, unread_only=True
            )
            == 2
        )
        # unread_only filter excludes the read one
        unread = await InAppNotificationRepo.list_for_user(
            db, ORG, USER_A, unread_only=True
        )
        assert n1_id not in {n.id for n in unread}
        updated = await InAppNotificationRepo.mark_all_read(db, ORG, USER_A)
        assert updated == 2
        await db.commit()
    async with factory() as db:
        assert (
            await InAppNotificationRepo.count_for_user(
                db, ORG, USER_A, unread_only=True
            )
            == 0
        )


async def test_mark_read_missing_returns_false(factory):
    async with factory() as db:
        assert (
            await InAppNotificationRepo.mark_read(db, ORG, USER_A, uuid.uuid4())
            is False
        )


async def test_delete(factory):
    async with factory() as db:
        n = await _make(db, USER_A)
        await db.commit()
        nid = n.id
    async with factory() as db:
        # Another user cannot delete it.
        assert await InAppNotificationRepo.delete(db, ORG, USER_B, nid) is False
        assert await InAppNotificationRepo.delete(db, ORG, USER_A, nid) is True
        await db.commit()
    async with factory() as db:
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 0


# --- emit service ------------------------------------------------------------


async def test_emit_creates_and_pushes(factory, monkeypatch):
    pushed: list = []

    async def fake_push(user_id, message):
        pushed.append((user_id, message.type))

    import backend.api.routes.ws as ws

    monkeypatch.setattr(ws, "publish_user", fake_push)

    async with factory() as db:
        n = await emit_notification(
            db,
            ORG,
            USER_A,
            event_type="incident.assigned",
            category=CATEGORY_INCIDENT,
            title="Assigned",
        )
        await db.commit()
        assert n is not None
    assert pushed == [(USER_A, "notification")]


async def test_emit_respects_mute(factory, monkeypatch):
    pushed: list = []

    async def fake_push(user_id, message):
        pushed.append(user_id)

    import backend.api.routes.ws as ws

    monkeypatch.setattr(ws, "publish_user", fake_push)

    async with factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            ORG,
            USER_A,
            routing={"in_app": {"muted_categories": [CATEGORY_INCIDENT]}},
        )
        await db.commit()
    async with factory() as db:
        n = await emit_notification(
            db,
            ORG,
            USER_A,
            event_type="incident.assigned",
            category=CATEGORY_INCIDENT,
            title="Assigned",
        )
        await db.commit()
        assert n is None
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 0
    assert pushed == []


async def test_emit_quiet_hours_stores_but_no_push(factory, monkeypatch):
    pushed: list = []

    async def fake_push(user_id, message):
        pushed.append(user_id)

    import backend.api.routes.ws as ws

    monkeypatch.setattr(ws, "publish_user", fake_push)

    async with factory() as db:
        # Quiet hours covering the whole day → push suppressed, row still stored.
        await UserNotificationPrefRepo.upsert(
            db,
            ORG,
            USER_A,
            quiet_hours={
                "enabled": True,
                "start": "00:00",
                "end": "23:59",
                "tz": "UTC",
            },
            quiet_hours_provided=True,
        )
        await db.commit()
    async with factory() as db:
        n = await emit_notification(
            db,
            ORG,
            USER_A,
            event_type="incident.assigned",
            category=CATEGORY_INCIDENT,
            title="Assigned",
        )
        await db.commit()
        assert n is not None
        assert await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 1
    assert pushed == []


# --- helpers -----------------------------------------------------------------


def _at(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 6, 18, h, m, tzinfo=timezone.utc)


def test_quiet_hours_non_wrapping():
    qh = {"enabled": True, "start": "09:00", "end": "17:00", "tz": "UTC"}
    assert _in_quiet_hours(qh, _at("12:00")) is True
    assert _in_quiet_hours(qh, _at("08:00")) is False
    assert _in_quiet_hours(qh, _at("17:00")) is False  # end exclusive


def test_quiet_hours_wrapping_past_midnight():
    qh = {"enabled": True, "start": "22:00", "end": "07:00", "tz": "UTC"}
    assert _in_quiet_hours(qh, _at("23:30")) is True
    assert _in_quiet_hours(qh, _at("06:00")) is True
    assert _in_quiet_hours(qh, _at("12:00")) is False


def test_quiet_hours_disabled_or_malformed():
    assert _in_quiet_hours(None, _at("12:00")) is False
    assert _in_quiet_hours({"enabled": False}, _at("12:00")) is False
    assert _in_quiet_hours({"enabled": True, "start": "x", "end": "y"}, _at("12:00")) is False
    assert _in_quiet_hours({"enabled": True}, _at("12:00")) is False


def test_muted_categories_parsing():
    assert _muted_categories(None) == set()
    assert _muted_categories({}) == set()
    assert _muted_categories({"in_app": {}}) == set()
    assert _muted_categories(
        {"in_app": {"muted_categories": ["incident", "session"]}}
    ) == {"incident", "session"}


# --- approval.requested hook -------------------------------------------------


async def test_org_user_ids_with_roles(factory):
    from backend.db.repos import UserRepo
    from backend.notifications.service import org_user_ids_with_roles

    async with factory() as db:
        await UserRepo.add_to_organization(db, USER_A, ORG, role="operator")
        await UserRepo.add_to_organization(db, USER_B, ORG, role="viewer")
        await db.commit()
    async with factory() as db:
        approvers = await org_user_ids_with_roles(db, ORG, ("admin", "operator"))
        assert approvers == [USER_A]


async def test_approval_request_notifies_approvers(factory, monkeypatch):
    import asyncio

    import backend.api.routes.ws as ws
    from backend.db.models import Incident, Session as SessionModel
    from backend.db.repos import ApprovalRequestRepo, UserRepo

    async def fake_push(user_id, message):
        return None

    monkeypatch.setattr(ws, "publish_user", fake_push)

    inc_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    async with factory() as db:
        await UserRepo.add_to_organization(db, USER_A, ORG, role="operator")
        await UserRepo.add_to_organization(db, USER_B, ORG, role="viewer")
        db.add(Incident(id=inc_id, org_id=ORG, title="DB down", description="x"))
        db.add(
            SessionModel(id=sess_id, org_id=ORG, incident_id=inc_id, tier=1)
        )
        await db.commit()

    from backend.approvals.service import ApprovalService

    service = ApprovalService(
        factory, org_id=ORG, timeout_seconds=5, poll_interval_seconds=0.02
    )
    task = asyncio.create_task(
        service.request_and_wait(
            session_id=sess_id,
            action={"tool": "kubectl_delete"},
            justification="Deleting a stuck pod",
        )
    )
    # Let it create the request + emit, then resolve so the task can finish.
    # 0.6s (was 0.15s) gives headroom so the assertion doesn't race the
    # background approval task under heavy full-suite load; still well under
    # the 5s approval timeout.
    await asyncio.sleep(0.6)
    async with factory() as db:
        # The operator was notified; the viewer was not.
        assert (
            await InAppNotificationRepo.count_for_user(db, ORG, USER_A) == 1
        )
        assert (
            await InAppNotificationRepo.count_for_user(db, ORG, USER_B) == 0
        )
        items = await InAppNotificationRepo.list_for_user(db, ORG, USER_A)
        assert items[0].event_type == "approval.requested"
        assert items[0].category == "approval"
        assert items[0].link == f"/dashboard/incidents/{inc_id}"
        assert items[0].incident_id == inc_id
        # resolve the pending request so request_and_wait returns
        pending = await ApprovalRequestRepo.list_pending(db, ORG, session_id=sess_id)
        await ApprovalRequestRepo.resolve(
            db, ORG, pending[0].id, status="approved", resolved_by=USER_A
        )
        await db.commit()
    # Under the full suite, SQLite connection scheduling can delay the final
    # poll after the request is resolved. Keep this well below the approval's
    # own timeout while avoiding a teardown-only timing failure.
    await asyncio.wait_for(task, timeout=5)


# --- @mention parsing --------------------------------------------------------


def test_parse_mentions_basic():
    from backend.notifications import parse_mentions

    assert parse_mentions("hey @alice and @Bob_1, ping @alice") == {"alice", "bob_1"}
    assert parse_mentions("nothing here") == set()
    assert parse_mentions(None) == set()


def test_parse_mentions_ignores_emails():
    from backend.notifications import parse_mentions

    # "me@host.com" must not be read as a mention of "host.com".
    assert parse_mentions("mail me@host.com but ping @real") == {"real"}
