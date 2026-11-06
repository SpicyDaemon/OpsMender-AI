"""Small HTTP helpers shared by native integration adapters."""

from __future__ import annotations

import re
import ssl
from typing import Any, Callable
from urllib.parse import quote, unquote

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


_DIGITS = re.compile(r"[0-9]+")


def _refused(name: str, text: str) -> ValueError:
    shown = text if len(text) <= 80 else text[:80] + "..."
    return ValueError(
        f"{name} cannot be {shown!r}: use a valid path with no traversal, "
        "unexpected separators or control characters."
    )


def _has_controls(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _path_text(value: Any, name: str) -> str:
    if _has_controls(str(value)):
        raise _refused(name, str(value))
    return required(value, name)


def _unsafe(text: str, *, allow_slash: bool = False) -> bool:
    """Recognize traversal through decoding and path-parameter stripping."""
    for _ in range(4):
        if "\\" in text or ("/" in text and not allow_slash) or _has_controls(text):
            return True
        if any(
            part.partition(";")[0].strip() in {".", ".."} for part in text.split("/")
        ):
            return True
        decoded = unquote(text)
        if decoded == text:
            return False
        text = decoded
    # Bound the work on heavily nested input. Normal paths stabilize sooner.
    return True


def validate_path(value: Any, name: str, *, allow_slash: bool = False) -> str:
    """Check a path before constructing a provider URL."""
    text = _path_text(value, name)
    if _unsafe(text, allow_slash=allow_slash):
        raise _refused(name, text)
    return text


def path_segment(value: Any, name: str, *, allow_slash: bool = False) -> str:
    """One URL path segment made from a caller's value (M1-147).

    Percent-encode separators and reject dot segments. The HTTP client can
    fold a bare dot segment before sending the request, which turned
    "delete the pod named ." into "delete every pod of the namespace".
    """
    return quote(validate_path(value, name, allow_slash=allow_slash), safe="")


def path_parts(value: Any, name: str = "value") -> list[str]:
    """The encoded segments of a path of several parts: a file path, a
    branch name, a job inside folders.

    Empty parts and ``.`` parts are dropped, since neither can move the
    path; a ``..`` part is refused, and so is a value with nothing else.
    Each part is encoded as it stands, spaces included, so the value can
    go deeper but never back up or sideways.
    """
    text = str(value or "")
    if not text.strip():
        raise ValueError(f"{name} is required")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(_unsafe(part) for part in parts):
        raise _refused(name, text)
    return [quote(part, safe="") for part in parts]


def path_value(value: Any, name: str = "value") -> str:
    """A path of several segments as one string (see ``path_parts``)."""
    return "/".join(path_parts(value, name))


def numeric_id(value: Any, name: str = "id") -> str:
    """A provider's numeric identifier as a path segment.

    A ticket, task or page id is digits; a word in its place would reach a
    sibling route of the same API (``tickets/recent``, ``tickets/update_many``).
    """
    text = _path_text(value, name)
    if not _DIGITS.fullmatch(text):
        raise ValueError(f"{name} must be a number, not {text[:80]!r}.")
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
