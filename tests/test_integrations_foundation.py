from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.audit.executor import audited_tool_call
from backend.db.models import Base, IntegrationConnector, Organization
from backend.db.repos import IntegrationConnectorRepo
from backend.integrations.base import IntegrationCapability
from backend.integrations.generic import GenericHTTPAdapter
from backend.integrations.tools import (
    IntegrationToolDescriptor,
    IntegrationToolRuntime,
    merge_integration_skill,
)
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import check


@pytest.fixture
async def integration_factory(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "integration-test-secret")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Integrations", slug="integrations"))
        await db.commit()
    yield factory, org_id
    await engine.dispose()


async def test_connector_auth_is_encrypted_at_rest(integration_factory):
    factory, org_id = integration_factory
    async with factory() as db:
        row = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="custom",
            name="Status API",
            base_url="https://status.example.com",
            auth_type="pat",
            auth={"token": "plain-secret"},
            config={},
            is_enabled=True,
        )
        await db.commit()
        connector_id = row.id
        assert row.auth_encrypted
        assert "plain-secret" not in row.auth_encrypted

    async with factory() as db:
        row = await IntegrationConnectorRepo.get_by_id(db, org_id, connector_id)
        assert IntegrationConnectorRepo.decrypt_auth(row) == {"token": "plain-secret"}
        assert (
            await IntegrationConnectorRepo.get_by_id(db, uuid.uuid4(), connector_id)
            is None
        )


async def test_generic_adapter_uses_mocked_http_and_auth_header():
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(204)

    adapter = GenericHTTPAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = IntegrationConnector(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind="custom",
        name="Mock",
        base_url="https://example.test",
        auth_type="pat",
        config={},
        is_enabled=True,
    )
    result = await adapter.safe_invoke(
        "test_connection", connector, {"token": "secret"}
    )
    assert result.ok is True
    assert result.data["status_code"] == 204
    assert seen["authorization"] == "Bearer secret"


def test_internal_tool_policy_is_explicit_and_fail_closed():
    connector_id = uuid.uuid4()
    descriptor = IntegrationToolDescriptor(
        name=f"integration__github__create_issue__{connector_id.hex}",
        description="Create an issue.",
        connector_id=connector_id,
        capability=IntegrationCapability(
            action="create_issue",
            description="Create an issue.",
            classification="caution",
            mutating=True,
        ),
    )
    skill = merge_integration_skill(
        SkillDefinition(version="1", environment="test", operations=[]),
        [descriptor],
    )
    assert check(descriptor.name, 0, skill).permitted is False
    tier_one = check(descriptor.name, 1, skill)
    assert tier_one.permitted is True
    assert tier_one.requires_approval is True
    assert check(descriptor.name, 2, skill).permitted is False


async def test_internal_tool_runtime_returns_mcp_shape_and_updates_status(
    integration_factory, monkeypatch
):
    factory, org_id = integration_factory

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    adapter = GenericHTTPAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    monkeypatch.setitem(
        __import__("backend.integrations.registry", fromlist=["_ADAPTERS"])._ADAPTERS,
        "custom",
        adapter,
    )
    async with factory() as db:
        await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="custom",
            name="Runtime",
            base_url="https://example.test",
            auth_type="none",
            auth=None,
            config={},
            is_enabled=True,
        )
        await db.commit()

    runtime = await IntegrationToolRuntime.create(factory, org_id)
    assert len(runtime.descriptors) == 1
    result = await runtime.call_tool(runtime, runtime.descriptors[0].name, {})
    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    async with factory() as db:
        rows = await IntegrationConnectorRepo.list_for_org(db, org_id)
        assert rows[0].status == "healthy"
        assert rows[0].last_checked_at is not None


async def test_runtime_enforces_strict_service_integration_allowlist(
    integration_factory,
):
    """Item 5 — a service's strict integration allowlist filters the tools.

    ``None`` exposes every enabled connector; a set restricts to it; an empty
    set exposes nothing (strict allowlist → no integrations)."""

    factory, org_id = integration_factory
    async with factory() as db:
        a = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="custom",
            name="Allowed",
            base_url="https://a.test",
            auth_type="none",
            auth=None,
            config={},
            is_enabled=True,
        )
        b = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="custom",
            name="Blocked",
            base_url="https://b.test",
            auth_type="none",
            auth=None,
            config={},
            is_enabled=True,
        )
        await db.commit()
        a_id, b_id = a.id, b.id

    # None → all connectors exposed (no service context / back-compat).
    everything = await IntegrationToolRuntime.create(factory, org_id)
    exposed = {d.connector_id for d in everything.descriptors}
    assert exposed == {a_id, b_id}

    # Non-empty allowlist → only the selected connector's tools.
    restricted = await IntegrationToolRuntime.create(
        factory, org_id, allowed_connector_ids={a_id}
    )
    assert {d.connector_id for d in restricted.descriptors} == {a_id}

    # Empty allowlist → no integration tools at all (strict semantics).
    none_allowed = await IntegrationToolRuntime.create(
        factory, org_id, allowed_connector_ids=set()
    )
    assert none_allowed.descriptors == []


async def test_audited_internal_tool_awaits_async_logger():
    connector_id = uuid.uuid4()
    descriptor = IntegrationToolDescriptor(
        name=f"integration__custom__test_connection__{connector_id.hex}",
        description="Test a connector.",
        connector_id=connector_id,
        capability=IntegrationCapability(
            action="test_connection",
            description="Test a connector.",
        ),
    )
    skill = merge_integration_skill(
        SkillDefinition(version="1", environment="test", operations=[]),
        [descriptor],
    )

    class Logger:
        def __init__(self):
            self.events: list[str] = []

        async def log_tool_call_start(self, *args, **kwargs):
            self.events.append("start")

        async def log_tool_call_end(self, *args, **kwargs):
            self.events.append("end")

        async def log_tool_call_blocked(self, *args, **kwargs):
            self.events.append("blocked")

    async def caller(session, tool_name, params):
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    logger = Logger()
    result = await audited_tool_call(
        session=object(),
        tool_name=descriptor.name,
        session_id=str(uuid.uuid4()),
        tier=0,
        skill_def=skill,
        logger=logger,
        tool_caller=caller,
    )
    assert result.permitted is True
    assert logger.events == ["start", "end"]
