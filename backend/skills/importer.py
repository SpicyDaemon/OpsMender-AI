"""Auto-import helper for operator-owned skill files under ``skills/``.

On backend startup we scan ``skills/**/*.md`` and insert any skills that
are not already present in the database (matched by ``name``).
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.repos import SkillRepo, OrganizationRepo
from backend.skills.parser import SkillDefinition, loads

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ImportResult:
    imported: list[str]
    skipped: list[str]
    failed: list[tuple[str, str]]  # (path, reason)


def _resolve_skill_name(path: pathlib.Path, root: pathlib.Path) -> str:
    rel = path.relative_to(root)
    parts = rel.parts
    if path.name.lower() == "skill.md" and len(parts) > 1:
        return parts[-2]
    return path.stem


def _candidate_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.md"))


async def auto_import(
    factory: async_sessionmaker[AsyncSession],
    skills_dir: pathlib.Path | str = "skills",
) -> ImportResult:
    """Import every ``*.md`` under *skills_dir* not already in the DB.

    For multi-tenant AIM, we import these into the first organization found (Main).
    """
    root = pathlib.Path(skills_dir)
    result = ImportResult(imported=[], skipped=[], failed=[])

    files = list(_candidate_files(root))
    if not files:
        logger.info("skills.auto_import: no files under %s", root)
        return result

    async with factory() as db:
        organizations = await OrganizationRepo.list_all(db)
        if not organizations:
            logger.error("skills.auto_import: no organizations found, cannot import")
            return result
        
        org_id = organizations[0].id

        for path in files:
            name = _resolve_skill_name(path, root)
            existing = await SkillRepo.get_by_name(db, org_id, name)
            if existing is not None:
                result.skipped.append(name)
                continue

            try:
                raw = path.read_text(encoding="utf-8")
                # Parse to validate format — content is persisted as-is.
                _ = loads(raw)
            except Exception as exc:  # noqa: BLE001 — surface to caller
                logger.warning("skills.auto_import: skip %s (%s)", path, exc)
                result.failed.append((str(path), str(exc)))
                continue

            await SkillRepo.create(
                db,
                org_id,
                name=name,
                content_md=raw,
                description=f"Auto-imported from {path}",
                mcp_server_id=None,
            )
            result.imported.append(name)

        await db.commit()

    if result.imported:
        logger.info(
            "skills.auto_import: imported %d skill(s): %s",
            len(result.imported),
            ", ".join(result.imported),
        )
    return result


def load_skill_definition(raw: str) -> SkillDefinition:
    """Convenience wrapper for ``loads`` used by enforcement callers."""
    return loads(raw)
