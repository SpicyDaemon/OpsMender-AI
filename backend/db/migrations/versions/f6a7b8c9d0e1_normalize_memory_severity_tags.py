"""Normalize incident-memory severity tags.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-06
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

_SEVERITY_LEVELS = frozenset({"critical", "high", "medium", "low"})


def _canonicalize_tag(tag: str) -> str:
    normalised = tag.strip().lower()
    if normalised in _SEVERITY_LEVELS:
        return f"severity-{normalised}"
    return normalised


def _normalize_tags(raw_tags: Any) -> Any:
    tags = raw_tags
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            return raw_tags
    if not isinstance(tags, list):
        return raw_tags

    normalised: list[Any] = []
    for tag in tags:
        value = _canonicalize_tag(tag) if isinstance(tag, str) else tag
        if value and value not in normalised:
            normalised.append(value)
    return normalised


def upgrade() -> None:
    memories = sa.table(
        "incident_memories",
        sa.column("id", sa.Uuid()),
        sa.column("tags", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(memories.c.id, memories.c.tags)).all()
    for row in rows:
        normalised = _normalize_tags(row.tags)
        if normalised == row.tags:
            continue
        bind.execute(
            memories.update()
            .where(memories.c.id == row.id)
            .values(tags=normalised)
        )


def downgrade() -> None:
    # Data-only normalization is intentionally not reversed.
    pass
