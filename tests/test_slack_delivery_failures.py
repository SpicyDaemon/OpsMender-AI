"""Slack transport failures must not escape or duplicate ambiguous sends."""

from types import SimpleNamespace

import httpx
import pytest

from backend.bots.connectors.slack import SlackAdapter


def failing_client(monkeypatch, failures):
    attempts = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            attempts.append(kwargs["json"])
            if failures:
                raise failures.pop(0)
            return httpx.Response(
                200, json={"ok": True, "channel": "C-test", "ts": "123.456"}
            )

    monkeypatch.setattr("backend.bots.connectors.slack.httpx.AsyncClient", Client)
    return attempts


async def send(operation):
    connector = SimpleNamespace(credentials={"bot_token": "synthetic-token"})
    result = await getattr(SlackAdapter(), operation)(
        connector, chat_id="C-test", text="synthetic completion"
    )
    return result if isinstance(result, tuple) else (result.ok, result.error)


@pytest.mark.parametrize("operation", ["send_message", "send_incident_update"])
@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ConnectError])
async def test_connection_failure_recovers_before_send(monkeypatch, operation, failure):
    attempts = failing_client(monkeypatch, [failure("private transport detail")])
    assert await send(operation) == (True, None)
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


@pytest.mark.parametrize("operation", ["send_message", "send_incident_update"])
async def test_connection_retry_exhaustion_is_safe(monkeypatch, operation):
    attempts = failing_client(
        monkeypatch, [httpx.ConnectTimeout("private transport detail")] * 4
    )
    ok, error = await send(operation)
    assert not ok
    assert error == "Slack transport error: ConnectTimeout"
    assert len(attempts) == 3


@pytest.mark.parametrize("operation", ["send_message", "send_incident_update"])
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.WriteTimeout])
async def test_ambiguous_send_is_not_retried(monkeypatch, operation, failure):
    attempts = failing_client(monkeypatch, [failure("private transport detail")])
    ok, error = await send(operation)
    assert not ok
    assert error == f"Slack transport error: {failure.__name__}"
    assert len(attempts) == 1
