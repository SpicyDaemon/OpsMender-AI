"""
Dev-mode backend launcher — no Postgres required.

Uses SQLite (same engine as the test suite), creates all tables,
seeds an admin user, then starts Uvicorn on port 8000.

Usage:
    uv run python scripts/dev_server.py

Default credentials:  admin / admin123
"""

import asyncio
import os
import sys

# Place project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_URL = "sqlite+aiosqlite:///./aim-dev.db"
os.environ["AIM_DATABASE_URL"] = DB_URL
os.environ.setdefault("AIM_JWT_SECRET", "dev-secret-not-for-production")
os.environ["AIM_CORS_ORIGINS"] = "*"  # dev only — wildcard is fine with Bearer token auth


async def bootstrap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.db.models import Base, User
    from backend.db.repos import UserRepo
    from backend.api.deps import set_session_factory

    engine = create_async_engine(DB_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("[dev] Tables created (or already exist).")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

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


if __name__ == "__main__":
    asyncio.run(bootstrap())

    import uvicorn
    from backend.api.app import create_app

    print("[dev] Starting backend on http://localhost:8000")
    print("[dev] API docs at  http://localhost:8000/docs")
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, reload=False)
