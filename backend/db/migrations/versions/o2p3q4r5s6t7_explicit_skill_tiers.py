"""Convert stored skills to explicit per-operation tier policies.

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-07-10
"""

from __future__ import annotations

import copy
import logging

from alembic import op
import sqlalchemy as sa
import yaml

revision = "o2p3q4r5s6t7"
down_revision = "n1o2p3q4r5s6"
branch_labels = None
depends_on = None

_LOGGER = logging.getLogger(__name__)
_TIERS = {
    "T0": {
        "enabled": True,
        "mode": "autonomous",
        "require_reversible": True,
    },
    "T1": {"enabled": True, "mode": "approval"},
    "T2": {"enabled": False, "mode": "advisory"},
}


def _convert_content(raw: str) -> str:
    lines = raw.splitlines(keepends=True)
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) >= 2:
        start, end = fences[0], fences[1]
        yaml_text = "".join(lines[start + 1 : end])
    else:
        start = end = -1
        yaml_text = raw

    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict):
        raise ValueError("skill YAML root is not a mapping")
    operations = data.get("operations", [])
    if not isinstance(operations, list):
        raise ValueError("skill operations are not a list")

    changed = False
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"operation {index + 1} is not a mapping")
        if bool(operation.get("deny", False)) or "tiers" in operation:
            continue
        operation["tiers"] = copy.deepcopy(_TIERS)
        changed = True
    if not changed:
        return raw

    dumped = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    if start < 0:
        return dumped
    return "".join(lines[: start + 1]) + dumped + lines[end] + "".join(lines[end + 1 :])


def upgrade() -> None:
    skills = sa.table(
        "skills",
        sa.column("id", sa.Uuid()),
        sa.column("content_md", sa.Text()),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(skills.c.id, skills.c.content_md)).all()
    for row in rows:
        try:
            converted = _convert_content(row.content_md)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("skill %s was left unchanged: %s", row.id, exc)
            continue
        if converted == row.content_md:
            continue
        bind.execute(
            skills.update().where(skills.c.id == row.id).values(content_md=converted)
        )


def downgrade() -> None:
    # Explicit policies are not safely reversible to classification-only data.
    pass
