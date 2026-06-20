"""Allow nullable usernames for email-first signup.

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "g3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("users_username_key", "users", type_="unique")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "username",
            existing_type=sa.String(length=150),
            nullable=True,
        )
    op.create_index(
        "ix_users_username_unique_not_null",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("username IS NOT NULL"),
        sqlite_where=sa.text("username IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_username_unique_not_null", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "username",
            existing_type=sa.String(length=150),
            nullable=False,
        )
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint("users_username_key", "users", ["username"])
