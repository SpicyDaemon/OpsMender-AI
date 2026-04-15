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


async def bootstrap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.config_loader import AppConfig
    from backend.db.models import Base, User
    from backend.db.engine import resolve_database_url
    from backend.db.repos import UserRepo

    config = AppConfig.load()
    db_url = resolve_database_url(config.db)
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("[dev] Tables created (or already exist).")

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed admin user if not present
    async with factory() as session:
        existing = await UserRepo.get_by_username(session, "admin")
        if existing is None:
            import bcrypt
            hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
            await UserRepo.create(
                session,
                username="admin",
                email="admin@localhost",
                password_hash=hashed,
                role="admin",
            )
            await session.commit()
            print("[dev] Seeded admin user: admin / admin123")
        else:
            print("[dev] Admin user already exists.")

    resolved_config = replace(config, db=replace(config.db, url=db_url))
    return resolved_config


if __name__ == "__main__":
    config = asyncio.run(bootstrap())

    import uvicorn
    from backend.api.app import create_app

    print("[dev] Starting backend on http://localhost:8000")
    print("[dev] API docs at  http://localhost:8000/docs")
    uvicorn.run(create_app(config), host="0.0.0.0", port=8000, reload=False)
