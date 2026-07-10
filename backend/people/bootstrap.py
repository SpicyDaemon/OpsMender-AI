"""First-install bootstrap admin (Sprint 56).

When the ``users`` table has zero rows AND
``OPSMENDER_BOOTSTRAP_ADMIN_EMAIL`` + ``OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD``
are both set, create:

1. A default organization (slug = "main") if none exists.
2. An admin user bound to that org.

Runs once at API startup. If any user already exists, the function is a
no-op — operators who later want to onboard a *new* admin do so through
the invite flow.

Development convenience: when the resolved environment is development and no
bootstrap env vars are set, fall back to a default ``admin`` / ``admin123``
admin so the documented ``docker compose up`` dev flow logs in out of the box
(matching ``scripts/dev_server.py`` and the README). Production mode never
seeds a default admin — it requires explicit bootstrap vars.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from backend.api.auth import hash_password
from backend.config_loader import is_development_environment
from backend.db.repos import OrganizationRepo, UserRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.config_loader import PeopleConfig

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"[^a-z0-9_-]+")

# Default dev admin — kept in sync with scripts/dev_server.py and the README.
_DEV_ADMIN_USERNAME = "admin"
_DEV_ADMIN_EMAIL = "admin@localhost"
_DEV_ADMIN_PASSWORD = "admin123"  # noqa: S105 — development-only convenience default


def _username_from_email(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    cleaned = _USERNAME_RE.sub("-", local).strip("-") or "admin"
    return cleaned[:150]


def _is_development_mode() -> bool:
    return is_development_environment()


async def bootstrap_admin(
    session_factory: "async_sessionmaker",
    cfg: "PeopleConfig",
) -> None:
    """Create the first admin when the system is empty.

    Uses the explicit ``OPSMENDER_BOOTSTRAP_ADMIN_*`` env vars when configured;
    otherwise, in development mode only, falls back to ``admin`` / ``admin123``
    so the documented Docker dev flow works without hidden steps. Does nothing
    when a user already exists, or when not configured outside development mode.
    """

    use_dev_default = not cfg.bootstrap_configured and _is_development_mode()
    if not cfg.bootstrap_configured and not use_dev_default:
        return

    async with session_factory() as db:
        existing = await UserRepo.list_all(db)
        if existing:
            return

        orgs = await OrganizationRepo.list_all(db)
        org = (
            orgs[0]
            if orgs
            else await OrganizationRepo.create(db, name="Main", slug="main")
        )

        if cfg.bootstrap_configured:
            assert cfg.bootstrap_admin_email is not None
            assert cfg.bootstrap_admin_password is not None
            email = cfg.bootstrap_admin_email
            password = cfg.bootstrap_admin_password
            username = _username_from_email(email)
        else:
            # Development-only convenience default (matches dev_server.py).
            email = _DEV_ADMIN_EMAIL
            password = _DEV_ADMIN_PASSWORD
            username = _DEV_ADMIN_USERNAME

        user = await UserRepo.create(
            db,
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            primary_org_id=org.id,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=org.id, role="admin"
        )
        await db.commit()

        if use_dev_default:
            logger.info(
                "Development bootstrap admin created (username=%s / default password). "
                "Set OPSMENDER_BOOTSTRAP_ADMIN_EMAIL + _PASSWORD to override.",
                username,
            )
        else:
            logger.info(
                "Bootstrap admin created (username=%s, email=%s, org=%s)",
                username,
                email,
                org.slug,
            )
