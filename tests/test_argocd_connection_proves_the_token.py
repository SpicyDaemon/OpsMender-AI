"""M1-145 (I-09, I-10): the Argo CD connection test proves the token.

In the 2026-11-05 live round, against Argo CD v3.6.1, Test reported an error
for a working scoped token: it read ``/api/v1/version``, which that server
answers 404. The route that exists, ``/api/version``, answers without any
token, so it cannot tell a good token from a bad one. And the server's
default self-signed certificate failed verification with raw OpenSSL text.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from backend.db.models import IntegrationConnector
from backend.integrations.automation import ArgoCDAdapter

GOOD = "scoped-token"


def _adapter(handler):
    requests: list[httpx.Request] = []

    async def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return await handler(request)

    adapter = ArgoCDAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(recording)
        )
    )
    return adapter, requests


def _argocd(*, readable=("intg1-bookinfo",)):
    """An Argo CD v3.6.1 as the live round met it."""

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"Version": "v3.6.1"})
        if path == "/api/v1/version":
            return httpx.Response(404, text="404 page not found")
        if request.headers.get("authorization") != f"Bearer {GOOD}":
            return httpx.Response(
                401,
                json={
                    "error": "invalid session",
                    "code": 16,
                    "message": "invalid session",
                },
            )
        if path == "/api/v1/applications":
            return httpx.Response(
                200, json={"items": [{"metadata": {"name": n}} for n in readable]}
            )
        name = path.removeprefix("/api/v1/applications/")
        if name in readable:
            return httpx.Response(200, json={"metadata": {"name": name}})
        return httpx.Response(
            403,
            json={
                "error": "permission denied",
                "code": 7,
                "message": "permission denied",
            },
        )

    return _adapter(handler)


def _connector(**config) -> IntegrationConnector:
    return IntegrationConnector(
        kind="argocd",
        name="Argo CD",
        base_url="http://argocd.example:8081",
        auth_type="pat",
        config=config,
    )


async def test_a_working_scoped_token_passes_on_the_configured_application():
    adapter, requests = _argocd()
    result = await adapter.safe_invoke(
        "test_connection", _connector(application="intg1-bookinfo"), {"token": GOOD}
    )
    # On main this was "Argo CD HTTP 404" for the same token.
    assert result.ok is True
    assert result.data["detail"] == (
        "Argo CD accepted the token; application 'intg1-bookinfo' is readable."
    )
    assert [str(r.url) for r in requests] == [
        "http://argocd.example:8081/api/v1/applications/intg1-bookinfo"
    ]


async def test_a_bad_token_fails_although_the_version_route_would_answer():
    adapter, requests = _argocd()
    for config in ({"application": "intg1-bookinfo"}, {}):
        result = await adapter.safe_invoke(
            "test_connection", _connector(**config), {"token": "wrong"}
        )
        assert result.ok is False
        assert result.error == (
            "Argo CD rejected the token (Argo CD HTTP 401: invalid session). "
            "Create a new token and save it in the connector."
        )
    # The token-free version route is never what decides.
    assert all(r.url.path.startswith("/api/v1/applications") for r in requests)


async def test_an_application_the_token_cannot_read_is_named():
    adapter, _ = _argocd()
    result = await adapter.safe_invoke(
        "test_connection", _connector(application="payments"), {"token": GOOD}
    )
    assert result.ok is False
    assert result.error == (
        "Argo CD accepted the token, but the application 'payments' was not "
        "found or the token may not read it (Argo CD HTTP 403: permission denied)."
    )


async def test_no_default_application_lists_what_the_token_sees():
    adapter, requests = _argocd(readable=("a", "b"))
    result = await adapter.safe_invoke("test_connection", _connector(), {"token": GOOD})
    assert result.ok is True
    assert result.data["detail"] == (
        "Argo CD accepted the token; 2 application(s) are visible to it. "
        "No default application is set."
    )
    # Names only: the list is not pulled in full for a probe.
    assert requests[0].url.params["fields"] == "items.metadata.name"


@pytest.mark.parametrize("config", [{"application": "payments"}, {}])
@pytest.mark.parametrize(
    "answer",
    [
        # The web interface or a sign-in page: 200, and not Argo CD's API.
        httpx.Response(200, text="<html>Argo CD</html>"),
        # A server that is something else entirely.
        httpx.Response(404, text="404 page not found"),
        # A proxy's own refusal.
        httpx.Response(403, text="<html>Forbidden</html>"),
    ],
)
async def test_an_answer_that_is_not_argo_cds_is_never_a_token_verdict(config, answer):
    """A page answering 200 used to read "accepted the token" with any
    token, and a wrong address with a default application set read as the
    application missing."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(answer.status_code, text=answer.text)

    adapter, _ = _adapter(handler)
    result = await adapter.safe_invoke(
        "test_connection", _connector(**config), {"token": "wrong"}
    )
    assert result.ok is False
    assert result.error == (
        "The base URL does not answer as an Argo CD API server "
        f"(HTTP {answer.status_code}, not JSON). Enter the address of argocd-server."
    )


async def test_json_that_is_not_the_application_or_the_list_is_not_healthy():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    adapter, _ = _adapter(handler)
    named = await adapter.safe_invoke(
        "test_connection", _connector(application="payments"), {"token": GOOD}
    )
    assert named.ok is False and "not with the application 'payments'" in named.error
    listed = await adapter.safe_invoke("test_connection", _connector(), {"token": GOOD})
    assert listed.ok is False and "application list" in listed.error


def _tls_failure(code: int, message: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        cause = ssl.SSLCertVerificationError(
            1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message}"
        )
        cause.verify_code = code
        cause.verify_message = message
        raise httpx.ConnectError(str(cause), request=request) from cause

    return _adapter(handler)[0]


async def test_an_untrusted_certificate_says_what_to_change():
    adapter = _tls_failure(18, "self-signed certificate")
    result = await adapter.safe_invoke(
        "test_connection", _connector(application="intg1-bookinfo"), {"token": GOOD}
    )
    assert result.ok is False
    assert result.error == (
        "Argo CD's TLS certificate is not trusted by OpsMender: it is "
        "self-signed or issued by a private certificate authority. Give the "
        "server a certificate from a trusted authority, or run OpsMender with "
        "SSL_CERT_FILE pointing at a bundle that holds your authority (or the "
        "server's own certificate when it is self-signed) and the public ones. "
        "Certificate verification stays on for this connector."
    )


async def test_a_certificate_for_another_name_or_time_gets_its_own_remedy():
    """Following the advice for a self-signed certificate does not help when
    the certificate names another host or has expired, which is likely for
    the one argocd-server ships with."""
    named = await _tls_failure(
        62, "Hostname mismatch, certificate is not valid for 'argocd.example'"
    ).safe_invoke("test_connection", _connector(), {"token": GOOD})
    assert named.error.startswith(
        "Argo CD's TLS certificate was issued for another name than the one in "
        "the base URL."
    )
    expired = await _tls_failure(10, "certificate has expired").safe_invoke(
        "test_connection", _connector(), {"token": GOOD}
    )
    assert expired.error.startswith(
        "Argo CD's TLS certificate has expired or is not valid yet."
    )
    other = await _tls_failure(24, "invalid CA certificate").safe_invoke(
        "test_connection", _connector(), {"token": GOOD}
    )
    assert other.error.startswith(
        "OpsMender could not verify Argo CD's TLS certificate: invalid CA certificate."
    )


async def test_a_refused_connection_is_not_dressed_up_as_a_certificate_problem():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    adapter, _ = _adapter(handler)
    result = await adapter.safe_invoke("test_connection", _connector(), {"token": GOOD})
    assert result.ok is False
    assert result.error == "All connection attempts failed"
