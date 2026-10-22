"""Card ordering across independent PostgreSQL connections on a scratch DB."""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import test_chat_card_state as cards

card_tasks = cards.card_tasks
client = cards.client
outbound = cards.outbound

pytestmark = pytest.mark.integration


@pytest.fixture
async def app(tmp_path, monkeypatch):
    url = os.environ.get("PART4_PG_URL", "")
    database = make_url(url).database if url else None
    assert database and database.startswith("qa_") and database != "testdb"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT current_database()")) == database
        await connection.run_sync(cards.Base.metadata.drop_all)
    await engine.dispose()
    monkeypatch.setattr(
        cards, "create_async_engine", lambda *a, **k: create_async_engine(url)
    )

    class SeparateProcesses:
        def setdefault(self, *args):
            # Each delivery has its own process turn, so only PostgreSQL can
            # serialize these separate checked-out connections.
            return asyncio.Lock()

    monkeypatch.setattr(cards.notifier, "_card_turns", SeparateProcesses())
    async for application in cards.app.__wrapped__(tmp_path, monkeypatch):
        yield application


@pytest.mark.parametrize("order", ["older_first", "newer_first", "overlap"])
async def test_postgres_card_order(
    client, app, outbound, card_tasks, monkeypatch, order
):
    await cards.test_two_queued_cards_keep_the_newer_owner(
        client, app, outbound, card_tasks, monkeypatch, order
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_postgres_failed_send(
    client, app, outbound, card_tasks, monkeypatch, cancel
):
    await cards.test_failed_card_send_releases_turn_without_advancing_event(
        client, app, outbound, card_tasks, monkeypatch, cancel
    )


async def test_postgres_turn_blocks_same_channel_only(app):
    factory = app.state.session_factory
    entered = asyncio.Event()

    async def next_delivery():
        async with cards.notifier._delivery_turn(factory, "card-test-same-channel"):
            entered.set()

    async with cards.notifier._delivery_turn(factory, "card-test-same-channel"):
        task = asyncio.create_task(next_delivery())
        try:
            async with asyncio.timeout(5):
                while True:
                    async with factory() as db:
                        waiting = await db.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks "
                                "WHERE locktype = 'advisory' AND NOT granted "
                                "AND database = (SELECT oid FROM pg_database "
                                "WHERE datname = current_database())"
                            )
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
            assert not entered.is_set()
            async with asyncio.timeout(5):
                async with cards.notifier._delivery_turn(
                    factory, "card-test-other-channel"
                ):
                    pass
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    await asyncio.wait_for(task, 5)
    assert entered.is_set()
