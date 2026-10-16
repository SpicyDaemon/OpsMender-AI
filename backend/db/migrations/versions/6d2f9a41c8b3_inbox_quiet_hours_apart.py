"""Store Inbox quiet hours apart from My Routing's page quiet hours.

``user_notification_prefs.quiet_hours`` held two settings in two shapes, and
each page's save replaced the other's (M1-59). The Inbox preferences page
wrote ``{enabled, start, end, tz}``, which holds back the live Inbox pop; My
Routing writes ``{weekday_start, weekday_end, days, min_priority_to_break,
time_zone}``, which holds P1 pages. Inbox quiet hours now live at
``routing.in_app.quiet_hours`` beside ``muted_categories``, and the column
keeps My Routing's page quiet hours only.

Data only, on every database, no schema change: an Inbox-shaped value (has
``enabled``, no ``weekday_start``) moves into ``routing.in_app.quiet_hours``
and the column is cleared on that row; page-shaped values stay. Downgrade
moves it back where the column is empty.

Revision ID: 6d2f9a41c8b3
Revises: z3a4b5c6d7e8
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "6d2f9a41c8b3"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _prefs() -> sa.TableClause:
    return sa.table(
        "user_notification_prefs",
        sa.column("id", sa.Uuid()),
        sa.column("routing", _JSON),
        sa.column("quiet_hours", _JSON),
    )


def _decode(value: Any) -> Any:
    """A JSON value as Python data; some drivers hand JSON back as text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _load(bind, prefs: sa.TableClause) -> list[tuple[Any, Any, Any]]:
    query = sa.select(prefs.c.id, prefs.c.routing, prefs.c.quiet_hours)
    return [
        (row.id, _decode(row.routing), _decode(row.quiet_hours))
        for row in bind.execute(query).all()
    ]


def upgrade() -> None:
    prefs = _prefs()
    bind = op.get_bind()
    for row_id, routing, quiet_hours in _load(bind, prefs):
        routing = routing or {}
        inbox_shaped = (
            isinstance(quiet_hours, dict)
            and "enabled" in quiet_hours
            and "weekday_start" not in quiet_hours
        )
        if not inbox_shaped or not isinstance(routing, dict):
            continue
        in_app = routing.get("in_app")
        in_app = dict(in_app) if isinstance(in_app, dict) else {}
        in_app["quiet_hours"] = quiet_hours
        bind.execute(
            prefs.update()
            .where(prefs.c.id == row_id)
            .values(routing={**routing, "in_app": in_app}, quiet_hours=sa.null())
        )


def downgrade() -> None:
    prefs = _prefs()
    bind = op.get_bind()
    for row_id, routing, quiet_hours in _load(bind, prefs):
        if quiet_hours is not None or not isinstance(routing, dict):
            continue
        in_app = routing.get("in_app")
        inbox = in_app.get("quiet_hours") if isinstance(in_app, dict) else None
        if not isinstance(inbox, dict):
            continue
        restored = {key: value for key, value in routing.items() if key != "in_app"}
        rest = {key: value for key, value in in_app.items() if key != "quiet_hours"}
        if rest:
            restored["in_app"] = rest
        bind.execute(
            prefs.update()
            .where(prefs.c.id == row_id)
            .values(routing=restored, quiet_hours=inbox)
        )
