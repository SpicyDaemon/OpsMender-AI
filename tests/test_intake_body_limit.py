"""M1-32 (R-07, O-12): intake refuses alert bodies over the limit (1 MiB by
default) with 413 before reading them in full, declared or streamed, and the
token's delivery log keeps a short row naming the size and the limit, never
the body. A body at the limit is accepted."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.config_loader import AppConfig, IngestConfig
from backend.db.models import Incident, IngestLog
from tests.test_ingest import (
    _create_paged_service,
    _create_token,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

MIB = 1024 * 1024


def _alert(size: int, alert_id: str = "big-1") -> bytes:
    """A JSON alert of exactly ``size`` bytes."""
    base = {"title": f"disk full {alert_id}", "severity": "high", "id": alert_id}
    base["description"] = ""
    padding = size - len(json.dumps(base).encode())
    assert padding >= 0
    base["description"] = "x" * padding
    body = json.dumps(base).encode()
    assert len(body) == size
    return body


async def _streamed(body: bytes, chunk: int = 64 * 1024):
    for start in range(0, len(body), chunk):
        yield body[start : start + chunk]


async def _route(app, client: AsyncClient, admin_headers, kind: str):
    """(url, headers) for a service intake URL or the token-authed webhook."""
    if kind == "service":
        service = await _create_paged_service(
            client,
            app,
            admin_headers,
            name=f"Big{uuid.uuid4().hex[:4]}",
            alert_grouping="off",
        )
        return service["intake_url"], {}
    raw, _ = await _create_token(app)
    return "/incidents/ingest", {"X-OpsMender-Token": raw}


async def _incidents(app) -> int:
    async with app.state.session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Incident))


async def _logs(app) -> list[IngestLog]:
    async with app.state.session_factory() as db:
        return list((await db.execute(select(IngestLog))).scalars())


def test_the_limit_defaults_to_one_mib_and_is_configurable(tmp_path):
    assert IngestConfig().max_body_bytes == MIB
    env = tmp_path / ".env"
    env.write_text("OPSMENDER_INGEST_MAX_BODY_BYTES=2048\n")
    assert AppConfig.load(env).ingest.max_body_bytes == 2048
    with pytest.raises(ValueError, match="must be >= 1024"):
        IngestConfig(max_body_bytes=100)


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_declared_body_over_the_limit_gets_413(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)
    body = _alert(MIB + 1)

    resp = await client.post(
        url, content=body, headers={**headers, "Content-Type": "application/json"}
    )

    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"] == "Alert body is over the 1,048,576-byte intake limit"
    assert await _incidents(app) == 0
    [log] = await _logs(app)
    assert log.raw_payload == {"body_bytes": MIB + 1, "limit_bytes": MIB}
    assert log.error == (
        "Body over the 1,048,576-byte intake limit (1,048,577 bytes); not read"
    )
    assert log.dedup_action == "skipped" and log.incident_id is None


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_streamed_body_over_the_limit_gets_413(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)

    resp = await client.post(
        url,
        content=_streamed(_alert(2 * MIB)),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert resp.request.headers.get("content-length") is None  # chunked
    assert resp.status_code == 413, resp.text
    assert await _incidents(app) == 0
    [log] = await _logs(app)
    assert log.raw_payload == {"body_bytes": None, "limit_bytes": MIB}
    assert log.error == (
        "Body over the 1,048,576-byte intake limit (more than that); not read"
    )


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_body_at_the_limit_is_accepted(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)

    declared = await client.post(
        url,
        content=_alert(MIB, "edge-1"),
        headers={**headers, "Content-Type": "application/json"},
    )
    streamed = await client.post(
        url,
        content=_streamed(_alert(MIB, "edge-2")),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert declared.status_code == streamed.status_code == 200
    assert (
        declared.json()["dedup_action"]
        == streamed.json()["dedup_action"]
        == ("created")
    )
    assert await _incidents(app) == 2


async def test_a_lower_limit_from_config_applies(
    app, client: AsyncClient, admin_headers
):
    app.state.config.ingest.max_body_bytes = 4096
    url, headers = await _route(app, client, admin_headers, "service")

    small = await client.post(url, content=_alert(4096, "ok-1"))
    big = await client.post(url, content=_alert(4097, "big-2"))

    assert (small.status_code, big.status_code) == (200, 413)
    assert big.json()["detail"] == "Alert body is over the 4,096-byte intake limit"
