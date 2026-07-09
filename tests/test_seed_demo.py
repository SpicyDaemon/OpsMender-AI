from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import pytest

from backend.config_loader import set_env_path
from backend.db.models import Service
from scripts import seed_demo


@pytest.mark.asyncio
async def test_seed_demo_services_have_mcp_allowlists(tmp_path, monkeypatch):
    db_file = tmp_path / "demo.db"
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv(
        "OPSMENDER_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    monkeypatch.setenv("OPSMENDER_BOOTSTRAP_ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD", "DemoSeed123!")

    set_env_path(env_file)
    try:
        await seed_demo.main()
    finally:
        set_env_path(None)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file.as_posix()}",
        echo=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            rows = (
                await db.execute(
                    select(Service.slug, Service.mcp_server_ids).order_by(Service.slug)
                )
            ).all()
    finally:
        await engine.dispose()

    expected_slugs = {
        "api-gateway",
        "auth-service",
        "checkout-api",
        "payments-db",
        "ingest-pipeline",
    }
    allowlists = {slug: mcp_ids for slug, mcp_ids in rows if slug in expected_slugs}

    assert set(allowlists) == expected_slugs
    assert all(allowlists[slug] for slug in expected_slugs)
    assert len(allowlists["checkout-api"]) >= 2
