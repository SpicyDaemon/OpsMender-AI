"""Tests for the TeamsGraphDMChannel (Sprint 37 step 2)."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.auth import graph_oauth
from backend.paging.channels import TeamsGraphDMChannel
from backend.paging.channel_factory import build_channel_factory


TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
SECRET = "secret"
CHAT_ID = "19:aaa@thread.v2"


@pytest.fixture(autouse=True)
def _reset_cache():
    graph_oauth.reset_token_cache()
    yield
    graph_oauth.reset_token_cache()


def _factory_for(handler):
    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport)

    return factory


class TestTeamsGraphDMChannel:
    async def test_happy_path_acquires_token_and_posts_message(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if "/oauth2/v2.0/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "graph-tok",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "/chats/" in str(request.url) and request.url.path.endswith("/messages"):
                return httpx.Response(201, json={"id": "1"})
            return httpx.Response(404)

        ch = TeamsGraphDMChannel(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=_factory_for(handler),
        )
        result = await ch.send(
            recipient=CHAT_ID,
            subject="db on fire",
            body="latency p99 spiked",
        )
        assert result.status == "sent"
        # Two HTTP calls: token + post.
        assert len(captured) == 2
        post_req = captured[1]
        assert CHAT_ID in str(post_req.url)
        assert post_req.headers["Authorization"] == "Bearer graph-tok"
        body = json.loads(post_req.content.decode("utf-8"))
        assert body["body"]["contentType"] == "html"
        assert "db on fire" in body["body"]["content"]
        assert "latency p99" in body["body"]["content"]

    async def test_graph_http_error_returns_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/oauth2/v2.0/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "t",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(403, json={"error": {"code": "Forbidden"}})

        ch = TeamsGraphDMChannel(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=_factory_for(handler),
        )
        result = await ch.send(recipient=CHAT_ID, subject="x", body="y")
        assert result.status == "failed"
        assert "Forbidden" in (result.error or "")

    async def test_oauth_error_returns_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "Bad secret",
                },
            )

        ch = TeamsGraphDMChannel(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=_factory_for(handler),
        )
        result = await ch.send(recipient=CHAT_ID, subject="x", body="y")
        assert result.status == "failed"
        assert "graph_oauth" in (result.error or "")
        assert "Bad secret" in (result.error or "")


class TestChannelFactoryWiring:
    def test_teams_dm_graph_returns_none_when_unconfigured(self):
        factory = build_channel_factory(env={})
        assert factory("teams_dm_graph") is None

    def test_teams_dm_graph_returns_channel_when_all_set(self):
        env = {
            "OPSMENDER_TEAMS_GRAPH_TENANT_ID": TENANT,
            "OPSMENDER_TEAMS_GRAPH_CLIENT_ID": CLIENT,
            "OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET": SECRET,
        }
        factory = build_channel_factory(env=env)
        ch = factory("teams_dm_graph")
        assert isinstance(ch, TeamsGraphDMChannel)

    def test_teams_dm_graph_requires_all_three_env_vars(self):
        env = {
            "OPSMENDER_TEAMS_GRAPH_TENANT_ID": TENANT,
            "OPSMENDER_TEAMS_GRAPH_CLIENT_ID": CLIENT,
            # secret missing
        }
        factory = build_channel_factory(env=env)
        assert factory("teams_dm_graph") is None
