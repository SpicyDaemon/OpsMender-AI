"""First-install bootstrap admin (Sprint 56).

When the ``users`` table has zero rows AND
``OPSMENDER_BOOTSTRAP_ADMIN_EMAIL`` + ``OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD``
are both set, create:

1. A default organization (slug = "main") if none exists.
2. An admin user bound to that org.

Runs once at API startup. If any user already exists, the function is a
no-op — operators who later want to onboard a *new* admin do so through
the invite flow.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from backend.api.auth import hash_password
from backend.db.repos import OrganizationRepo, UserRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.config_loader import PeopleConfig

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"[^a-z0-9_-]+")


def _username_from_email(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    cleaned = _USERNAME_RE.sub("-", local).strip("-") or "admin"
    return cleaned[:150]


async def bootstrap_admin(
    session_factory: "async_sessionmaker",
    cfg: "PeopleConfig",
) -> None:
    """Create the first admin from env vars when the system is empty."""

    if not cfg.bootstrap_configured:
        return

    async with session_factory() as db:
        existing = await UserRepo.list_all(db)
        if existing:
            return

        orgs = await OrganizationRepo.list_all(db)
        org = orgs[0] if orgs else await OrganizationRepo.create(
            db, name="Main", slug="main"
        )

        assert cfg.bootstrap_admin_email is not None
        assert cfg.bootstrap_admin_password is not None
        username = _username_from_email(cfg.bootstrap_admin_email)

        user = await UserRepo.create(
            db,
            username=username,
            email=cfg.bootstrap_admin_email,
            password_hash=hash_password(cfg.bootstrap_admin_password),
            role="admin",
            primary_org_id=org.id,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=org.id, role="admin"
        )
        await db.commit()

        logger.info(
            "Bootstrap admin created (username=%s, email=%s, org=%s)",
            username,
            cfg.bootstrap_admin_email,
            org.slug,
        )
