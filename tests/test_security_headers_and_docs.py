"""Production hardening: API docs exposure + security response headers.

From the 2026-07-03 security audit (docs/SECURITY_AUDIT_2026-07-03.md):
- /docs, /redoc and /openapi.json enumerate the full API attack surface and
  must be off by default in production (OPSMENDER_ENABLE_API_DOCS opts back
  in; development keeps them on).
- Every response carries conservative browser-hardening headers.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.app import create_app
from backend.config_loader import set_env_path


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Build create_app() against a minimal env; env vars set per-test win."""

    def _make(**env: str):
        tmp_env = tmp_path / ".env"
        tmp_env.write_text("OPSMENDER_TIER=2\n")
        set_env_path(tmp_env)
        monkeypatch.setenv("OPSMENDER_JWT_SECRET", "a-strong-test-secret-value")
        monkeypatch.setenv(
            "OPSMENDER_DATABASE_URL",
            "postgresql+asyncpg://opsmender:test@db:5432/opsmender",
        )
        monkeypatch.setenv("OPSMENDER_MCP_SERVERS_JSON", json.dumps([]))
        monkeypatch.delenv("OPSMENDER_ENABLE_API_DOCS", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return create_app()

    yield _make
    set_env_path(None)


async def _get(app, path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


class TestAPIDocsExposure:
    async def test_docs_enabled_in_development(self, make_app):
        app = make_app(OPSMENDER_ENVIRONMENT="development")
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await _get(app, path)).status_code == 200, path

    async def test_docs_disabled_in_production(self, make_app):
        app = make_app(OPSMENDER_ENVIRONMENT="production")
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = await _get(app, path)
            # 404 from the API (or the frontend catch-all when a build is
            # mounted) — anything but the live docs/schema.
            assert (
                response.status_code != 200
                or "openapi" not in response.text[:200].lower()
            ), path

    async def test_docs_opt_in_in_production(self, make_app):
        app = make_app(
            OPSMENDER_ENVIRONMENT="production",
            OPSMENDER_ENABLE_API_DOCS="true",
        )
        for path in ("/docs", "/openapi.json"):
            assert (await _get(app, path)).status_code == 200, path


class TestSecurityHeaders:
    async def test_headers_present_on_api_response(self, make_app):
        app = make_app(OPSMENDER_ENVIRONMENT="development")
        response = await _get(app, "/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    async def test_headers_present_on_404(self, make_app):
        app = make_app(OPSMENDER_ENVIRONMENT="development")
        response = await _get(app, "/definitely-not-a-route")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
