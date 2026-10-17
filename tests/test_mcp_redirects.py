"""M1-66: with mcp 1.30 a remote MCP server is followed through a redirect only
within its own origin; OpsMender names any other redirect instead of chasing
it, and its own OAuth client refuses authorization server metadata whose
issuer is not the one asked for."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from backend.config_loader import MCPServerConfig
from backend.mcp.client import MCPClientError, connect
from backend.mcp.oauth import MCPOAuthError, fetch_authz_server_metadata


class _Elsewhere(BaseHTTPRequestHandler):
    """Answers every request with a redirect to another port on this host."""

    other_port = 0

    def _redirect(self) -> None:
        self.send_response(307)
        self.send_header("Location", f"http://127.0.0.1:{self.other_port}/mcp")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _redirect

    def log_message(self, *args) -> None:  # noqa: D102 - quiet test server
        pass


@pytest.fixture
def redirecting_server():
    server = HTTPServer(("127.0.0.1", 0), _Elsewhere)
    _Elsewhere.other_port = server.server_port + 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_a_redirect_to_another_origin_is_named_not_followed(
    redirecting_server, transport
):
    url = f"{redirecting_server}/mcp"
    config = MCPServerConfig(name="elsewhere", transport=transport, url=url)

    async def attempt():
        async with connect(config):
            pass  # pragma: no cover

    with pytest.raises(MCPClientError) as refused:
        asyncio.run(attempt())
    message = str(refused.value)
    assert message.startswith(f"The MCP server at {redirecting_server} redirected to ")
    assert f"http://127.0.0.1:{_Elsewhere.other_port}" in message
    assert "enter it as the server URL" in message


async def test_metadata_with_another_issuer_is_refused():
    issuer = "https://auth.example.com"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": "https://attacker.example",
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "code_challenge_methods_supported": ["S256"],
            },
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(MCPOAuthError, match="issuer mismatch"):
        await fetch_authz_server_metadata(issuer, http_client_factory=factory)
