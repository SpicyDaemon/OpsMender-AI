"""Add skills.assignment + remap legacy Tier 3 to Tier 2.

3-tier AI Autonomy model:
  - skills gain an ``assignment`` column: "server" | "global" | "unassigned".
    Existing rows are backfilled: skills bound to an MCP server -> "server",
    others -> "global" (preserving the old NULL = global-fallback behaviour).
  - any stored runtime-config ``tier`` value of "3" (legacy advise-only) is
    remapped to "2" (advisory only).

Revision ID: v1w2x3y4z5a6
Revises: u0v1w2x3y4z5
Create Date: 2026-06-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "v1w2x3y4z5a6"
down_revision = "u0v1w2x3y4z5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "assignment",
            sa.String(length=20),
            nullable=False,
            server_default="global",
        ),
    )
    # Backfill: server-bound skills -> "server"; the rest keep "global".
    op.execute(
        "UPDATE skills SET assignment = 'server' WHERE mcp_server_id IS NOT NULL"
    )
    # Remap any stored Tier 3 default to Tier 2 (advisory only).
    op.execute(
        "UPDATE runtime_config SET value = '2' WHERE key = 'tier' AND value = '3'"
    )


def downgrade() -> None:
    op.drop_column("skills", "assignment")
