"""
Dev-mode backend launcher — no Postgres required.

Loads the shared env-based config, picks the same DB fallback chain
as the app, creates all tables, seeds an admin user, then starts
Uvicorn on port 8000.

Usage:
    uv run python scripts/dev_server.py

Default credentials:  admin / admin123
"""

import asyncio
import os
import sys
from dataclasses import replace

# Place project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Sprint 43 P0 #4 — opt into the development bypass so the production
# default-secret guard does not refuse to start the local dev server.
os.environ.setdefault("OPSMENDER_DEPLOYMENT_MODE", "development")


async def bootstrap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.config_loader import AppConfig
    from backend.db.bootstrap import initialize_sqlite_schema
    from backend.db.engine import resolve_database_url
    from backend.db.repos import OrganizationRepo, UserRepo

    config = AppConfig.load()
    db_url = resolve_database_url(config.db)
    engine = create_async_engine(db_url, echo=False)

    if db_url.startswith("sqlite"):
        await initialize_sqlite_schema(
            engine,
            reporter=lambda message: print(f"[dev] {message}"),
        )
    else:
        from backend.db.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    print("[dev] Tables created (or already exist).")

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed default organization, admin user, and the user→org link row.
    # Idempotent — runs on every startup so older local DBs that pre-date the
    # multi-tenancy refactor get backfilled without a manual reset.
    async with factory() as session:
        # 1. Default org
        orgs = await OrganizationRepo.list_all(session)
        if not orgs:
            org = await OrganizationRepo.create(session, name="Main", slug="main")
            await session.commit()
            print(f"[dev] Seeded default organization: {org.name} ({org.slug})")
        else:
            org = orgs[0]

        # 2. Admin user
        existing = await UserRepo.get_by_username(session, "admin")
        if existing is None:
            import bcrypt

            hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
            existing = await UserRepo.create(
                session,
                username="admin",
                email="admin@localhost",
                password_hash=hashed,
                role="admin",
                primary_org_id=org.id,
            )
            await session.commit()
            print("[dev] Seeded admin user: admin / admin123")
        else:
            print("[dev] Admin user already exists.")

        # 3. user_organizations link (backfill for older DBs where the admin was
        # seeded before this seed handled multi-tenancy).
        if not await UserRepo.is_member(session, existing.id, org.id):
            await UserRepo.add_to_organization(
                session, user_id=existing.id, org_id=org.id, role="admin"
            )
            await session.commit()
            print(f"[dev] Linked admin to organization {org.name}")
        if existing.primary_org_id is None:
            await UserRepo.set_primary_org(session, existing.id, org.id)
            await session.commit()
            print(f"[dev] Set admin primary_org_id → {org.name}")

    resolved_config = replace(config, db=replace(config.db, url=db_url))
    return resolved_config


if __name__ == "__main__":
    config = asyncio.run(bootstrap())

    import uvicorn
    from backend.api.app import create_app

    print("[dev] Starting backend on http://localhost:8000")
    print("[dev] API docs at  http://localhost:8000/docs")
    uvicorn.run(create_app(config), host="0.0.0.0", port=8000, reload=False)
