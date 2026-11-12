"""Keep what a later-round approval request was written from (M1-166).

A Tier 1 session may ask an operator for an approval in a later planning
round, which is planned from what the session's tools returned. The request
then keeps the calls the session had made when it asked, and the results
found to address the assistant, so the operator sees them beside the
request. The column is NULL for a request of a first plan and for every
request made before this migration.

Revision ID: b6c7d8e9f0a1
Revises: f5a6b7c8d9e0
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.add_column(sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.drop_column("evidence")
