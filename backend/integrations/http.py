"""Small HTTP helpers shared by native integration adapters."""

from __future__ import annotations

import ssl
from typing import Any, Callable

import httpx

from backend.integrations.base import IntegrationResult

HttpClientFactory = Callable[[], httpx.AsyncClient]


def default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, follow_redirects=True)


def required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


# OpenSSL's verification results that have their own remedy.
_CERT_WRONG_NAME = {62, 64}  # host name or IP address mismatch
_CERT_WRONG_TIME = {9, 10}  # not yet valid, expired
_CERT_UNTRUSTED = {18, 19, 20, 21}  # self-signed, or no known issuer


def untrusted_certificate(
    provider: str, exc: BaseException
) -> IntegrationResult | None:
    """A readable result when a provider's certificate fails verification.

    Verification stays on. The failure otherwise surfaces as raw OpenSSL
    text that says nothing about what to change, and the three common
    causes need three different changes.
    """
    verify: ssl.SSLCertVerificationError | None = None
    cause: BaseException | None = exc
    for _ in range(8):
        if cause is None:
            break
        if isinstance(cause, ssl.SSLCertVerificationError):
            verify = cause
            break
        cause = cause.__cause__ or cause.__context__
    if verify is None and "CERTIFICATE_VERIFY_FAILED" not in str(exc):
        return None
    code = getattr(verify, "verify_code", None)
    reason = " ".join(str(getattr(verify, "verify_message", "") or "").split())
    kept = " Certificate verification stays on for this connector."
    if code in _CERT_WRONG_NAME:
        return IntegrationResult.failure(
            f"{provider}'s TLS certificate was issued for another name than "
            "the one in the base URL. Use a name the certificate lists, or "
            f"give the server a certificate for this one.{kept}"
        )
    if code in _CERT_WRONG_TIME:
        return IntegrationResult.failure(
            f"{provider}'s TLS certificate has expired or is not valid yet. "
            f"Renew it on the server.{kept}"
        )
    if code is None or code in _CERT_UNTRUSTED:
        return IntegrationResult.failure(
            f"{provider}'s TLS certificate is not trusted by OpsMender: it is "
            "self-signed or issued by a private certificate authority. Give "
            "the server a certificate from a trusted authority, or run "
            "OpsMender with SSL_CERT_FILE pointing at a bundle that holds "
            "your authority (or the server's own certificate when it is "
            f"self-signed) and the public ones.{kept}"
        )
    return IntegrationResult.failure(
        f"OpsMender could not verify {provider}'s TLS certificate"
        + (f": {reason[:160]}" if reason else "")
        + f".{kept}"
    )


# A provider's own words in an error are kept short: they go to the
# operator, the model and the audit log, and the provider chooses them.
_ERROR_DETAIL_LIMIT = 300


def response_error(provider: str, response: httpx.Response) -> IntegrationResult:
    detail = None
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = (
                body.get("message")
                or body.get("error")
                or body.get("error_description")
                or body.get("errorMessages")
            )
            errors = body.get("errors")
            if not detail and isinstance(errors, list):
                # The shape of Confluence's v2 API and of several others:
                # a list of objects with a title, a detail or a message.
                detail = [
                    item.get("title") or item.get("detail") or item.get("message")
                    for item in errors[:3]
                    if isinstance(item, dict)
                ]
                detail = [item for item in detail if item]
    except ValueError:
        pass
    if isinstance(detail, list):
        detail = "; ".join(str(item) for item in detail)
    if detail:
        detail = " ".join(str(detail).split())
        if len(detail) > _ERROR_DETAIL_LIMIT:
            detail = detail[:_ERROR_DETAIL_LIMIT].rstrip() + "..."
    return IntegrationResult.failure(
        f"{provider} HTTP {response.status_code}" + (f": {detail}" if detail else "")
    )
