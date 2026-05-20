"""Add client_id + client_secret_encrypted to mcp_server_oauth_tokens (Sprint 42 step 5).

Revision ID: f1a2b3c4d5e6
Revises: e9a1b2c3d4e5
Create Date: 2026-05-19 15:00:00.000000

Sprint 42 step 5 — the token-refresh path needs to authenticate against
the authorization server's token endpoint. For DCR-created clients the
client_id was embedded in the state JWT during authorization but never
persisted; this migration stores it (and an optional encrypted
client_secret) on the token row so the refresh call can reconstruct the
``ClientRegistration`` without re-running DCR.

  * ``client_id`` — plain text; client IDs are not secret.
  * ``client_secret_encrypted`` — Fernet-encrypted; nullable, because
    public clients (no secret) are the common DCR case.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e9a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_server_oauth_tokens",
        sa.Column("client_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "mcp_server_oauth_tokens",
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_server_oauth_tokens", "client_secret_encrypted")
    op.drop_column("mcp_server_oauth_tokens", "client_id")
