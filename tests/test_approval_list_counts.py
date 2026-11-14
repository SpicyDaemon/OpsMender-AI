"""The approval list's total counts every request that matches its filter.

M1-172: the sidebar's Approvals badge asks the list for one pending request
and reads its ``total``. The route set ``total`` to the number of requests
in the answer, so the badge said 1 whenever anything was pending. The
Approvals page asks for 100 requests, and its subtitle, which reads the
same total, said at most 100. ``total`` is now the number of requests that
match the status and session filter, whatever the page's ``limit`` and
``offset``.
"""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from backend.db.models import Organization
from backend.db.repos import ApprovalRequestRepo, SessionRepo
from tests.test_api import TEST_ORG_ID, app, auth_headers, client  # noqa: F401

OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000172")


async def _requests(app, statuses, *, org_id=TEST_ORG_ID):
    """One approval request for each entry in statuses, all in one new
    session."""
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(db, org_id, tier=1)
        for status in statuses:
            request = await ApprovalRequestRepo.create(
                db,
                org_id,
                session_id=session.id,
                action={"tool_name": "restart_service", "tool_parameters": {}},
                justification="The service stopped answering",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            if status != "pending":
                assert await ApprovalRequestRepo.resolve(
                    db, org_id, request.id, status=status
                )
        await db.commit()
        return session


async def _list(client, headers, query):
    resp = await client.get(f"/approvals?{query}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_total_counts_every_pending_request_past_the_page(
    client, app, auth_headers
):
    # What the sidebar asks: one pending request and the total.
    await _requests(app, ["pending", "pending", "pending", "pending", "approved"])

    data = await _list(client, auth_headers, "status=pending&limit=1")

    assert len(data["items"]) == 1
    assert data["total"] == 4
    assert data["status_counts"] == {"pending": 4, "approved": 1}


async def test_the_total_is_right_past_the_pages_100_rows(client, app, auth_headers):
    # What the Approvals page asks under its default Pending filter: 100
    # pending requests.
    await _requests(app, ["pending"] * 101)

    data = await _list(client, auth_headers, "status=pending&limit=100")

    assert len(data["items"]) == 100
    assert data["total"] == 101


async def test_every_page_of_a_list_reports_the_same_total(client, app, auth_headers):
    await _requests(app, ["pending", "pending", "pending"])

    pages = [
        await _list(client, auth_headers, f"status=pending&limit=2&offset={offset}")
        for offset in (0, 2, 5)
    ]

    assert [len(page["items"]) for page in pages] == [2, 1, 0]
    assert [page["total"] for page in pages] == [3, 3, 3]


async def test_without_a_status_the_total_counts_every_request(
    client, app, auth_headers
):
    await _requests(app, ["pending", "pending", "approved", "rejected", "expired"])

    data = await _list(client, auth_headers, "limit=1")

    assert len(data["items"]) == 1
    assert data["total"] == 5
    assert (await _list(client, auth_headers, "status=rejected&limit=1"))["total"] == 1


async def test_a_status_no_request_has_lists_nothing_and_totals_zero(
    client, app, auth_headers
):
    await _requests(app, ["pending", "approved"])

    # An empty status is a status too: no request has it.
    for query in ("status=unknown", "status="):
        data = await _list(client, auth_headers, query)

        assert data["items"] == []
        assert data["total"] == 0
        assert data["status_counts"] == {"pending": 1, "approved": 1}


async def test_the_total_of_one_session_counts_that_sessions_requests(
    client, app, auth_headers
):
    mine = await _requests(app, ["pending", "pending", "approved"])
    await _requests(app, ["pending", "pending", "pending", "pending"])

    everything = await _list(client, auth_headers, f"session_id={mine.id}&limit=1")
    waiting = await _list(
        client, auth_headers, f"session_id={mine.id}&status=pending&limit=1"
    )

    assert everything["total"] == 3
    assert waiting["total"] == 2
    assert {item["session_id"] for item in waiting["items"]} == {str(mine.id)}


async def test_another_organizations_requests_are_not_counted(
    client, app, auth_headers
):
    async with app.state.session_factory() as db:
        db.add(Organization(id=OTHER_ORG_ID, name="Other Org", slug="other-org"))
        await db.commit()
    await _requests(app, ["pending", "pending", "pending"], org_id=OTHER_ORG_ID)
    await _requests(app, ["pending"])

    data = await _list(client, auth_headers, "status=pending&limit=1")

    assert data["total"] == 1
    assert data["status_counts"] == {"pending": 1}
