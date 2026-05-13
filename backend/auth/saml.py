"""SAML 2.0 SP helper used by the per-tenant SAML SSO flow (Sprint 30).

Wraps ``python3-saml`` (OneLogin) to keep the surface Opsmender uses small:

* :func:`build_settings` — assemble the dict ``OneLogin_Saml2_Auth`` expects
  from the per-tenant DB row + the global SP keypair from env.
* :func:`build_authn_request` — return the IdP redirect URL for an
  SP-initiated login (used by ``GET /auth/saml/{slug}/login``).
* :func:`process_acs` — validate the IdP's POSTed AuthnResponse (signature,
  audience, NotOnOrAfter, replay) and extract attributes (used by
  ``POST /auth/saml/{slug}/acs``).
* :func:`render_sp_metadata` — produce the SP metadata XML the IdP admin
  uploads to set up the integration.
* :func:`fetch_idp_metadata` — async fetch + 10-minute cache for IdP
  metadata URLs; falls back to inline XML when the org provides that
  instead of a URL.

This module never talks to the DB directly. The route layer fetches the
``OrgSAMLConfig`` row, hands it in, and persists nothing back here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
from onelogin.saml2.settings import OneLogin_Saml2_Settings


_METADATA_TTL_SECONDS = 600
_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class SAMLError(Exception):
    """Raised when the SAML config is invalid or the IdP response fails
    validation. The message is safe to surface to operators."""


@dataclass
class SPKeypair:
    """The SP-side signing keypair shared across all tenants.

    Loaded once from env vars (``OPSMENDER_SAML_SP_CERT`` / ``OPSMENDER_SAML_SP_KEY``)
    and threaded through to every per-tenant ``build_settings`` call.
    """

    cert: str
    key: str
    entity_id_override: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.cert) and bool(self.key)


@dataclass
class SAMLOrgConfig:
    """Per-tenant fields needed to build SAML settings, decoupled from the
    SQLAlchemy row so this module stays import-cycle-free."""

    org_slug: str
    is_active: bool
    idp_metadata_url: str | None
    idp_metadata_xml: str | None
    email_attribute: str
    name_attribute: str
    want_assertions_signed: bool
    want_response_signed: bool


@dataclass
class _RequestData:
    """Subset of the FastAPI request that ``OneLogin_Saml2_Auth`` needs."""

    https: bool
    http_host: str
    server_port: int
    request_uri: str
    get_data: dict[str, Any] = field(default_factory=dict)
    post_data: dict[str, Any] = field(default_factory=dict)


def _request_dict(req: _RequestData) -> dict[str, Any]:
    return {
        "https": "on" if req.https else "off",
        "http_host": req.http_host,
        "server_port": str(req.server_port),
        "script_name": "",
        "request_uri": req.request_uri,
        "get_data": req.get_data,
        "post_data": req.post_data,
    }


async def fetch_idp_metadata(org: SAMLOrgConfig) -> dict[str, Any]:
    """Resolve the IdP metadata to the dict shape ``python3-saml`` expects.

    Two sources, mutually exclusive:

    * ``idp_metadata_url`` — fetched over HTTPS and cached in-process for
      10 minutes (mirrors the OIDC discovery cache).
    * ``idp_metadata_xml`` — used verbatim, no fetch.
    """
    if org.idp_metadata_url:
        url = org.idp_metadata_url
        now = time.time()
        cached = _metadata_cache.get(url)
        if cached and cached[0] > now:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise SAMLError(f"Failed to fetch IdP metadata: {exc}") from exc
        if resp.status_code != 200:
            raise SAMLError(
                f"IdP metadata URL returned {resp.status_code}"
            )
        try:
            parsed = OneLogin_Saml2_IdPMetadataParser.parse(resp.text)
        except Exception as exc:
            raise SAMLError(f"Failed to parse IdP metadata XML: {exc}") from exc
        idp = parsed.get("idp")
        if not idp:
            raise SAMLError("IdP metadata XML did not contain an EntityDescriptor")
        _metadata_cache[url] = (now + _METADATA_TTL_SECONDS, idp)
        return idp

    if org.idp_metadata_xml:
        try:
            parsed = OneLogin_Saml2_IdPMetadataParser.parse(org.idp_metadata_xml)
        except Exception as exc:
            raise SAMLError(f"Failed to parse IdP metadata XML: {exc}") from exc
        idp = parsed.get("idp")
        if not idp:
            raise SAMLError("IdP metadata XML did not contain an EntityDescriptor")
        return idp

    raise SAMLError(
        "SAML config has neither idp_metadata_url nor idp_metadata_xml"
    )


def _sp_section(sp_keypair: SPKeypair, org_slug: str, base_url: str) -> dict[str, Any]:
    entity_id = (
        sp_keypair.entity_id_override
        or f"{base_url}/auth/saml/{org_slug}/metadata"
    )
    return {
        "entityId": entity_id,
        "assertionConsumerService": {
            "url": f"{base_url}/auth/saml/{org_slug}/acs",
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
        "x509cert": sp_keypair.cert,
        "privateKey": sp_keypair.key,
    }


def build_settings(
    *,
    sp_keypair: SPKeypair,
    org: SAMLOrgConfig,
    base_url: str,
    idp: dict[str, Any],
) -> OneLogin_Saml2_Settings:
    """Compose the settings dict and instantiate ``OneLogin_Saml2_Settings``.

    ``idp`` is the dict returned by :func:`fetch_idp_metadata`.
    ``base_url`` is the public-facing SP URL (e.g. ``https://opsmender.acme.com``).
    """
    if not sp_keypair.configured:
        raise SAMLError(
            "SAML SP keypair is not configured. Set OPSMENDER_SAML_SP_CERT and "
            "OPSMENDER_SAML_SP_KEY (use `opsmender saml gen-sp-keys` to generate them)."
        )

    settings_dict: dict[str, Any] = {
        "strict": True,
        "debug": False,
        "sp": _sp_section(sp_keypair, org.org_slug, base_url),
        "idp": idp,
        "security": {
            "wantAssertionsSigned": org.want_assertions_signed,
            "wantMessagesSigned": org.want_response_signed,
            "authnRequestsSigned": True,
            "logoutRequestSigned": False,
            "logoutResponseSigned": False,
            "signMetadata": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        },
    }
    return OneLogin_Saml2_Settings(settings_dict, custom_base_path=None)


def _auth(
    request_data: _RequestData, settings: OneLogin_Saml2_Settings
) -> OneLogin_Saml2_Auth:
    return OneLogin_Saml2_Auth(_request_dict(request_data), old_settings=settings)


def build_authn_request(
    *,
    settings: OneLogin_Saml2_Settings,
    request_data: _RequestData,
    relay_state: str,
) -> str:
    """Return the IdP redirect URL (HTTP-Redirect binding) for SP-initiated SSO."""
    auth = _auth(request_data, settings)
    return auth.login(return_to=relay_state)


def process_acs(
    *,
    settings: OneLogin_Saml2_Settings,
    request_data: _RequestData,
    expected_relay_state: str | None = None,
) -> tuple[dict[str, list[str]], str | None]:
    """Validate the POSTed AuthnResponse and return ``(attributes, name_id)``.

    Raises :class:`SAMLError` on any validation failure (signature, expiry,
    audience, replay).
    """
    auth = _auth(request_data, settings)
    auth.process_response()
    errors = auth.get_errors()
    if errors:
        last = auth.get_last_error_reason() or "unknown"
        raise SAMLError(
            f"SAML response validation failed: {', '.join(errors)} ({last})"
        )
    if not auth.is_authenticated():
        raise SAMLError("SAML response did not authenticate the user")

    if expected_relay_state is not None:
        actual = (request_data.post_data or {}).get("RelayState")
        if actual is not None and actual != expected_relay_state:
            raise SAMLError("RelayState mismatch")

    return auth.get_attributes(), auth.get_nameid()


def first_attribute(
    attrs: dict[str, list[str]], name: str, fallback_keys: list[str] | None = None
) -> str | None:
    """Return the first attribute value for ``name``, falling back to common
    aliases (e.g. plain ``email`` if the configured FQ name claim is empty)."""
    keys = [name] + (fallback_keys or [])
    for k in keys:
        values = attrs.get(k)
        if values:
            for v in values:
                if v:
                    return str(v).strip()
    return None


def render_sp_metadata(settings: OneLogin_Saml2_Settings) -> str:
    """Return the SP metadata XML the IdP admin uploads."""
    metadata = settings.get_sp_metadata()
    errors = settings.validate_metadata(metadata)
    if errors:
        raise SAMLError(f"SP metadata validation failed: {', '.join(errors)}")
    return metadata if isinstance(metadata, str) else metadata.decode("utf-8")


def split_base_url(public_url: str) -> tuple[str, bool, str, int, str]:
    """Decompose a public Opsmender URL into the parts ``OneLogin_Saml2_Auth`` needs.

    Returns ``(base_url_without_path, https, host, port, request_uri)``.
    """
    parsed = urlparse(public_url)
    https = parsed.scheme == "https"
    host = parsed.hostname or ""
    port = parsed.port or (443 if https else 80)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    return base_url, https, host, port, parsed.path or "/"


def reset_caches() -> None:
    """Clear the IdP metadata cache (test-only helper)."""
    _metadata_cache.clear()
