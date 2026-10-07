"""M1-46 (R-18): a report schedule keeps its configured day and local time. A
monthly report for the 31st runs on the last day of shorter months and comes
back to the 31st, and a 09:00 New York report stays at 09:00 across both
clock changes."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from backend.reports.scheduler import advance_cadence, local_anchor
from tests.test_api_tokens import (
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

NEW_YORK = "America/New_York"


def _local(value: datetime, zone: str = NEW_YORK) -> tuple:
    there = value.astimezone(ZoneInfo(zone))
    return there.year, there.month, there.day, there.hour, there.minute


def _runs(first: datetime, cadence: str, count: int, zone: str) -> list[tuple]:
    day, clock = local_anchor(first, zone)
    runs, value = [], first
    for _ in range(count):
        value = advance_cadence(
            value, cadence, time_zone=zone, run_day=day, run_time=clock
        )
        runs.append(_local(value, zone))
    return runs


def test_a_report_for_the_31st_comes_back_after_short_months():
    first = datetime(2027, 1, 31, 9, 0, tzinfo=ZoneInfo(NEW_YORK))

    assert _runs(first, "monthly", 4, NEW_YORK) == [
        (2027, 2, 28, 9, 0),
        (2027, 3, 31, 9, 0),
        (2027, 4, 30, 9, 0),
        (2027, 5, 31, 9, 0),
    ]


def test_quarterly_keeps_its_day_through_february():
    first = datetime(2026, 11, 30, 9, 0, tzinfo=ZoneInfo(NEW_YORK))

    assert _runs(first, "quarterly", 3, NEW_YORK) == [
        (2027, 2, 28, 9, 0),
        (2027, 5, 30, 9, 0),
        (2027, 8, 30, 9, 0),
    ]


@pytest.mark.parametrize(
    "first",
    [
        # Across the spring change (2027-03-14) and the fall change (2027-11-07).
        datetime(2027, 3, 1, 9, 0, tzinfo=ZoneInfo(NEW_YORK)),
        datetime(2027, 10, 25, 9, 0, tzinfo=ZoneInfo(NEW_YORK)),
    ],
    ids=["spring", "fall"],
)
def test_weekly_stays_at_its_local_time_across_clock_changes(first):
    runs = _runs(first, "weekly", 4, NEW_YORK)

    assert [run[3:] for run in runs] == [(9, 0)] * 4
    utc_hours = {
        advance_cadence(first, "weekly", step, time_zone=NEW_YORK)
        .astimezone(timezone.utc)
        .hour
        for step in range(1, 5)
    }
    assert len(utc_hours) == 2  # the UTC hour moves; the local one doesn't


def test_a_period_ends_where_the_last_one_started():
    end = datetime(2027, 3, 31, 13, 0, tzinfo=timezone.utc)

    start = advance_cadence(end, "monthly", -1, time_zone=NEW_YORK, run_day=31)

    assert _local(start) == (2027, 2, 28, 9, 0)


async def test_the_api_keeps_the_zone_day_and_time(client: AsyncClient, admin_headers):
    first = datetime(2027, 1, 31, 9, 0, tzinfo=ZoneInfo(NEW_YORK))
    body = {
        "name": "Monthly digest",
        "cadence": "monthly",
        "recipients": ["reports@example.test"],
        "next_run_at": first.isoformat(),
        "time_zone": NEW_YORK,
    }

    created = await client.post("/reports/schedules", json=body, headers=admin_headers)
    unknown = await client.post(
        "/reports/schedules",
        json={**body, "name": "Bad zone", "time_zone": "Mars/Olympus"},
        headers=admin_headers,
    )

    assert created.status_code == 201, created.text
    saved = created.json()
    assert (saved["time_zone"], saved["run_day"], saved["run_time"]) == (
        NEW_YORK,
        31,
        "09:00",
    )
    assert unknown.status_code == 422
