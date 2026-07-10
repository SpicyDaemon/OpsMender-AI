"""Phase 1 — auto-open + link tickets on incident creation (per-service)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.integrations  # noqa: F401 - register bundled adapters
from backend.db.models import (
    Base,
    Incident,
    Organization,
    Service,
    Team,
)
from backend.db.repos import (
    IncidentIntegrationLinkRepo,
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.integrations.base import IntegrationAdapter, IntegrationResult
from backend.integrations.registry import _ADAPTERS
from backend.services.ticket_sync import (
    provision_incident_tickets,
    sync_incident_status,
)


class FakeJiraAdapter(IntegrationAdapter):
    kind = "jira"

    def __init__(self) -> None:
        self.created: list[str] = []
        self.synced: list[str] = []

    async def test_connection(self, connector, auth):
        return IntegrationResult.success()

    async def create_issue(
        self, connector, auth, summary, description=None, incident_id=None
    ):
        self.created.append(summary)
        key = f"OPS-{len(self.created)}"
        return IntegrationResult.success(
            issue={"key": key},
            integration_link={
                "incident_id": incident_id,
                "reference_type": "ticket",
                "external_id": key,
                "url": f"https://jira.example/browse/{key}",
                "title": summary,
            },
            ticket_sync={
                "incident_id": incident_id,
                "external_ticket_id": key,
                "external_ticket_url": f"https://jira.example/browse/{key}",
            },
        )

    async def sync_status_out(self, connector, auth, ticket_id, new_status):
        self.synced.append(f"{ticket_id}:{new_status}")
        return IntegrationResult.success(transitioned=True)


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "ticket-provision-test-secret")
    fake = FakeJiraAdapter()
    monkeypatch.setitem(_ADAPTERS, "jira", fake)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Org", slug="org"))
        team = Team(org_id=org_id, name="Team", slug="team")
        db.add(team)
        await db.flush()
        connector = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="jira",
            name="Jira OPS",
            base_url="https://jira.example",
            auth_type="pat",
            auth={"api_token": "t"},
            config={
                "ticket_sync_enabled": True,
                "project_key": "OPS",
                "status_map": {"open": "In Progress", "resolved": "Done"},
            },
            is_enabled=True,
        )
        await db.flush()
        team_id, connector_id = team.id, connector.id
        await db.commit()
    yield factory, org_id, team_id, connector_id, fake
    await engine.dispose()


async def _make_incident(factory, org_id, team_id, *, allowed, overrides=None):
    async with factory() as db:
        service = Service(
            org_id=org_id,
            team_id=team_id,
            name="DevOps",
            slug=f"svc-{uuid.uuid4().hex[:6]}",
            allowed_integration_connector_ids=[str(c) for c in allowed],
            integration_action_overrides=overrides or {},
        )
        db.add(service)
        await db.flush()
        incident = Incident(
            org_id=org_id,
            title="DB pool exhausted",
            description="connections maxed",
            status="open",
            priority="P1",
            service_id=service.id,
        )
        db.add(incident)
        await db.flush()
        incident_id = incident.id
        await db.commit()
    return incident_id


async def test_opens_and_links_a_ticket_for_allowlisted_connector(env):
    factory, org_id, team_id, connector_id, fake = env
    incident_id = await _make_incident(factory, org_id, team_id, allowed=[connector_id])

    created = await provision_incident_tickets(
        factory, org_id=org_id, incident_id=incident_id
    )
    assert created == 1
    assert fake.created == ["DB pool exhausted"]
    # The freshly opened ticket is pushed to the incident's "open" mapped status.
    assert fake.synced == ["OPS-1:In Progress"]

    async with factory() as db:
        states = await TicketSyncStateRepo.list_for_incident(db, org_id, incident_id)
        links = await IncidentIntegrationLinkRepo.list_for_incident(
            db, org_id, incident_id
        )
    assert [s.external_ticket_id for s in states] == ["OPS-1"]
    assert [link.external_id for link in links] == ["OPS-1"]


async def test_is_idempotent_no_duplicate_tickets(env):
    factory, org_id, team_id, connector_id, fake = env
    incident_id = await _make_incident(factory, org_id, team_id, allowed=[connector_id])
    assert (
        await provision_incident_tickets(
            factory, org_id=org_id, incident_id=incident_id
        )
        == 1
    )
    # Second dispatch must not open another ticket.
    assert (
        await provision_incident_tickets(
            factory, org_id=org_id, incident_id=incident_id
        )
        == 0
    )
    assert len(fake.created) == 1


async def test_respects_the_service_allowlist(env):
    factory, org_id, team_id, connector_id, fake = env
    # Connector exists + sync-enabled, but the service does NOT allow it.
    incident_id = await _make_incident(factory, org_id, team_id, allowed=[])
    created = await provision_incident_tickets(
        factory, org_id=org_id, incident_id=incident_id
    )
    assert created == 0
    assert fake.created == []


async def test_per_service_override_disables_auto_ticketing(env):
    factory, org_id, team_id, connector_id, fake = env
    # Connector is allowed + sync-enabled, but the service opts this connector
    # out of the ticket lifecycle — it stays available to the agent, no auto-open.
    incident_id = await _make_incident(
        factory,
        org_id,
        team_id,
        allowed=[connector_id],
        overrides={str(connector_id): {"ticket_lifecycle": False}},
    )
    created = await provision_incident_tickets(
        factory, org_id=org_id, incident_id=incident_id
    )
    assert created == 0
    assert fake.created == []


async def test_acknowledged_transitions_the_ticket(env):
    factory, org_id, team_id, connector_id, fake = env
    incident_id = await _make_incident(factory, org_id, team_id, allowed=[connector_id])
    await provision_incident_tickets(factory, org_id=org_id, incident_id=incident_id)
    fake.synced.clear()  # drop the initial "open" sync from provisioning

    await sync_incident_status(
        factory, org_id=org_id, incident_id=incident_id, new_status="acknowledged"
    )
    # The connector's status_map has no explicit "acknowledged", so the kind
    # default (In Progress) applies.
    assert fake.synced == ["OPS-1:In Progress"]
    async with factory() as db:
        states = await TicketSyncStateRepo.list_for_incident(db, org_id, incident_id)
    assert states[0].last_synced_status == "acknowledged"


async def test_no_backward_move_guardrail(env):
    factory, org_id, team_id, connector_id, fake = env
    incident_id = await _make_incident(factory, org_id, team_id, allowed=[connector_id])
    await provision_incident_tickets(factory, org_id=org_id, incident_id=incident_id)
    # Drive it forward to resolved, then attempt a backward move to open.
    await sync_incident_status(
        factory, org_id=org_id, incident_id=incident_id, new_status="resolved"
    )
    fake.synced.clear()
    await sync_incident_status(
        factory, org_id=org_id, incident_id=incident_id, new_status="open"
    )
    # Backward move (resolved -> open) is skipped — no transition attempted.
    assert fake.synced == []
    async with factory() as db:
        states = await TicketSyncStateRepo.list_for_incident(db, org_id, incident_id)
    assert states[0].last_synced_status == "resolved"
