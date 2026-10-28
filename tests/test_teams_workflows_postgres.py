"""M1-100: one post limit across processes, on a scratch PostgreSQL database.

Two engines stand in for two OpsMender processes, and each delivery gets its
own process turn, so only PostgreSQL's transaction turn on the channel can
keep the count, the post and its record together. The workflow is an
``httpx.MockTransport``; nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.bots import notifier
from backend.db.models import Base, Organization
from tests import test_teams_workflows_delivery as delivery

workflow = delivery.workflow

pytestmark = pytest.mark.integration


@pytest.fixture
async def factories(monkeypatch):
    url = os.environ.get("PART4_PG_URL", "")
    database = make_url(url).database if url else None
    # Hosted verification owns a fresh PostgreSQL service for this job.
    assert database and database != "testdb"
    assert database.startswith("qa_") or (
        os.environ.get("GITHUB_ACTIONS") == "true" and database == "opsmender"
    ), "Use a disposable qa_ database locally"
    engines = [create_async_engine(url), create_async_engine(url)]
    async with engines[0].begin() as connection:
        assert await connection.scalar(text("SELECT current_database()")) == database
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    makers = [async_sessionmaker(engine, expire_on_commit=False) for engine in engines]
    async with makers[0]() as db:
        db.add(Organization(id=delivery.ORG, name="Acme", slug="acme"))
        await db.commit()

    class SeparateProcesses:
        def setdefault(self, *args):
            # Each delivery has its own process turn, so only PostgreSQL can
            # serialize these separate checked-out connections.
            return asyncio.Lock()

    monkeypatch.setattr(notifier, "_card_turns", SeparateProcesses())
    yield makers
    async with engines[0].begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    for engine in engines:
        await engine.dispose()


async def test_two_processes_share_one_post_limit(factories, workflow):
    first, second = factories
    connector = await delivery._channel(first)
    incidents = [
        await delivery._incident(first, title=f"Incident {index}")
        for index in range(30)
    ]

    await asyncio.gather(
        *(
            delivery._event(first if index % 2 else second, incident.id)
            for index, incident in enumerate(incidents)
        )
    )

    assert len(workflow.posted) == 20
    statuses = [row.status for row in await delivery._audit(first, connector)]
    assert statuses.count("ok") == 20
    assert statuses.count("held") == 10


async def test_two_processes_catch_up_once(factories, workflow):
    first, second = factories
    connector = await delivery._channel(first)
    await delivery._seed(first, connector, 20)
    for index in range(3):
        incident = await delivery._incident(first, title=f"Held {index}")
        await delivery._event(first, incident.id)
    await delivery._age(first, connector)

    await asyncio.gather(
        notifier.post_held_cards(first), notifier.post_held_cards(second)
    )
    assert sorted(delivery._headline(card) for card in workflow.posted) == [
        "🚨 New incident: Held 0",
        "🚨 New incident: Held 1",
        "🚨 New incident: Held 2",
    ]
