"""Add mcp_server_oauth_tokens table (Sprint 42 step 1).

Revision ID: e9a1b2c3d4e5
Revises: d8e9f0a1b2c3
Create Date: 2026-05-19 14:00:00.000000

Sprint 42 step 1 — persistence for OAuth 2.1 token bundles obtained
from HTTP-transport MCP servers per the Model Context Protocol
authorization spec (RFC 9728 PRM discovery + RFC 8414 authz server
metadata + RFC 8707 Resource Indicators + RFC 9207 issuer validation
+ PKCE S256 + refresh-token rotation per OAuth 2.1 §4.3.1).

Schema:
  * One row per (org_id, mcp_server_id) — enforced by UNIQUE on
    ``mcp_server_id`` (which is itself org-scoped via the mcp_servers
    table). Cascade delete on the parent server cleans up the token.
  * ``access_token_encrypted`` + ``refresh_token_encrypted`` use the
    project's Fernet helper (``backend/auth/secrets.py``); both stored
    as URL-safe-base64 ASCII text.
  * ``refresh_token_encrypted`` is nullable — the MCP authz spec
    explicitly says clients MUST NOT assume refresh tokens are issued
    (§6.4).
  * ``issuer`` captures the authorization-server issuer recorded at
    authorize-request time, used for RFC 9207 mix-up-attack mitigation.
  * ``scopes`` is the JSON-encoded list of granted scopes (may differ
    from requested scopes per the spec's Scope Selection Strategy).
  * ``(org_id, expires_at)`` index supports the auto-refresh sweep
    query that finds tokens about to expire.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_oauth_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mcp_server_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "token_type",
            sa.String(length=32),
            nullable=False,
            server_default="Bearer",
        ),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column(
            "obtained_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_refreshed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mcp_server_id", name="uq_mcp_server_oauth_tokens_server"
        ),
    )

    op.create_index(
        "ix_mcp_server_oauth_tokens_org_expires",
        "mcp_server_oauth_tokens",
        ["org_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_server_oauth_tokens_org_expires",
        table_name="mcp_server_oauth_tokens",
    )
    op.drop_table("mcp_server_oauth_tokens")
