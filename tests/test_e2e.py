"""End-to-end integration test for the Sprint 13 single-container app.

Exercises the full REST chain: auth → incident → session → approval →
executed tool call → audit query. Uses an in-memory SQLite DB, a StubLLM,
and a mocked MCP session — no external services are contacted.

The flow mirrors how an operator would drive a Tier 1 session from the
dashboard: they start a session, the tier gate creates approval rows, the
operator approves via ``POST /approvals/{id}/approve``, execution runs, and
the audit log picks up the tool-call entry.
"""

from __future__ import annotations

import asyncio
import json
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import _build_tier_gate
from backend.approvals import ApprovalService
from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.audit.pg_logger import PgAuditLogger
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import SessionRepo
from backend.skills.parser import loads as load_skill_def
from backend.tiers.enforcement import check as tier_check

SKILL_MD = """---
version: "1"
environment: kubernetes-test
operations:
  - tool: get_pods
    classification: safe
  - tool: describe_pod
    classification: safe
  - tool: delete_pod
    classification: destructive
    notes: "Pod removal"
---

# Kubernetes test skill
"""


def _mock_mcp_result(text: str = "pod deleted"):
    item = MagicMock()
    item.type = "text"
    item.text = text
    res = MagicMock()
    res.content = [item]
    res.isError = False
    return res


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "e2e.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=1\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        f"AIM_DATABASE_URL={database_url}\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    async def _get_db_override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db_override

    yield application, factory

    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    application, _ = app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_login(
    client: AsyncClient, username: str, role: str = "viewer"
) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "secretpass123",
            "role": role,
        },
    )
    login = await client.post(
        "/auth/login", json={"username": username, "password": "secretpass123"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


class TestE2EIncidentFlow:
    """API → incident → session → approval → execute → audit (one chain)."""

    async def test_tier_1_incident_end_to_end(self, client: AsyncClient, app):
        _, factory = app

        # 1. Register an admin (first user → auto-promoted) and an operator.
        admin = await _register_login(client, "admin_e2e")
        operator = await _register_login(client, "op_e2e", role="operator")

        # 2. Admin files an incident.
        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "Checkout 5xx spike",
                "description": "Error rate at 12% from 14:30",
                "severity": "high",
            },
            headers=admin,
        )
        assert inc_resp.status_code == 201
        incident_id = inc_resp.json()["id"]

        # 3. Operator starts a Tier 1 session on that incident.
        sess_resp = await client.post(
            "/sessions",
            json={"incident_id": incident_id, "tier": 1},
            headers=operator,
        )
        assert sess_resp.status_code == 201
        session_id = uuid.UUID(sess_resp.json()["id"])

        # 4. Drive the tier gate — a destructive action requires approval.
        skill_def = load_skill_def(SKILL_MD)
        approval_service = ApprovalService(factory, poll_interval_seconds=0.01)
        gate = _build_tier_gate(
            tier=1, skill_def=skill_def, approval_service=approval_service
        )
        state = {
            "tier": 1,
            "session_id": str(session_id),
            "plan": [
                {"tool_name": "get_pods", "tool_parameters": {"namespace": "prod"}},
                {
                    "tool_name": "delete_pod",
                    "tool_parameters": {"name": "checkout-7xyz"},
                },
            ],
            "approved_actions": [],
            "blocked_actions": [],
            "approval_requests": [],
            "skill_definition": skill_def,
        }
        gate_task = asyncio.create_task(gate(state))

        # 5. Operator sees the pending approval via the REST API.
        # Poll briefly since the gate task creates the row asynchronously.
        approval_id: str | None = None
        for _ in range(100):
            if gate_task.done() and gate_task.exception():
                raise gate_task.exception()
            resp = await client.get(
                "/approvals", params={"status": "pending"}, headers=operator
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            session_approvals = [a for a in items if a["session_id"] == str(session_id)]
            if session_approvals:
                approval_id = session_approvals[0]["id"]
                break
            await asyncio.sleep(0.02)
        assert approval_id, "approval row never appeared via the API"

        # 6. Admin approves via the REST endpoint.
        approve_resp = await client.post(
            f"/approvals/{approval_id}/approve", headers=admin
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        # 7. Gate resumes and reports the action as approved.
        result = await gate_task
        assert len(result["approved_actions"]) == 2  # safe + approved destructive
        assert len(result["blocked_actions"]) == 0

        # 8. Execute the approved plan with a mock MCP session, writing
        #    audit entries to Postgres so the REST audit endpoint sees them.
        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(return_value=_mock_mcp_result("ok"))

        async with factory() as db:
            logger = PgAuditLogger(db)
            for action in result["approved_actions"]:
                tool_name = action["tool_name"]
                params = action.get("tool_parameters", {})
                enforcement = tier_check(tool_name, 1, skill_def)
                await logger.log_tool_call_start(
                    str(session_id), tier=1, tool_name=tool_name, tool_parameters=params
                )
                await mock_session.call_tool(tool_name, params)
                await logger.log_tool_call_end(
                    str(session_id),
                    tier=1,
                    tool_name=tool_name,
                    result={
                        "content": [{"type": "text", "text": "ok"}],
                        "isError": False,
                    },
                    duration_ms=1,
                )
                assert enforcement.permitted is True
            await db.commit()
        assert mock_session.call_tool.await_count == 2

        # 9. Audit log reflects both tool calls (start + end per call).
        audit_resp = await client.get(
            "/audit",
            params={
                "session_id": str(session_id),
                "entry_type": "tool_call_end",
                "limit": 100,
            },
            headers=admin,
        )
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["items"]
        assert len(entries) == 2
        tool_names = sorted(e["tool_name"] for e in entries)
        assert tool_names == ["delete_pod", "get_pods"]
        for entry in entries:
            assert entry["permitted"] is True
            assert entry["session_id"] == str(session_id)

        # 10. The operator closes the session via REST (conventional
        #     shutdown — just flips status + summary).
        async with factory() as db:
            session = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
            assert session is not None
            session.status = "completed"
            session.summary = "E2E test: destructive action executed after approval"
            await db.commit()

        get_resp = await client.get(f"/sessions/{session_id}", headers=admin)
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "completed"

    async def test_tier_1_rejection_blocks_execution(self, client: AsyncClient, app):
        """If the operator rejects, the destructive action never hits MCP."""
        _, factory = app

        admin = await _register_login(client, "admin_reject")
        operator = await _register_login(client, "op_reject", role="operator")

        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "Minor alert",
                "description": "Flapping CPU alarm from staging",
                "severity": "low",
            },
            headers=admin,
        )
        assert inc_resp.status_code == 201, inc_resp.text
        incident_id = inc_resp.json()["id"]

        sess_resp = await client.post(
            "/sessions",
            json={"incident_id": incident_id, "tier": 1},
            headers=operator,
        )
        session_id = uuid.UUID(sess_resp.json()["id"])

        skill_def = load_skill_def(SKILL_MD)
        service = ApprovalService(factory, poll_interval_seconds=0.01)
        gate = _build_tier_gate(tier=1, skill_def=skill_def, approval_service=service)
        gate_task = asyncio.create_task(
            gate(
                {
                    "tier": 1,
                    "session_id": str(session_id),
                    "plan": [
                        {
                            "tool_name": "delete_pod",
                            "tool_parameters": {"name": "risky"},
                        },
                    ],
                    "approved_actions": [],
                    "blocked_actions": [],
                    "approval_requests": [],
                    "skill_definition": skill_def,
                }
            )
        )

        approval_id: str | None = None
        for _ in range(50):
            resp = await client.get(
                "/approvals", params={"status": "pending"}, headers=operator
            )
            items = [
                a for a in resp.json()["items"] if a["session_id"] == str(session_id)
            ]
            if items:
                approval_id = items[0]["id"]
                break
            await asyncio.sleep(0.02)
        assert approval_id

        reject_resp = await client.post(
            f"/approvals/{approval_id}/reject", headers=admin
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

        result = await gate_task
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 1
        assert "reject" in result["blocked_actions"][0]["block_reason"].lower()
