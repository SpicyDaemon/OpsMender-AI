"""Microsoft Teams (Workflows) Notification Channel adapter (M1-100).

The admin pastes the URL of a Teams workflow made from the "Send webhook
alerts to a channel" (or "... to a chat") template. Each incident change
POSTs one Adaptive Card to it; the card's buttons are signed links (M1-99)
that open OpsMender's confirm page, so nothing acts inside Teams and a posted
card is never edited. Outbound only: nothing calls back from Teams.

The URL is a bearer secret. It is stored encrypted and never becomes a chat
id; no error, log line or check carries it, its path, its query or its
``sig``. Errors name the HTTP status or the exception type only, since they
land in the audit log, the channel's last error and the settings page.

An optional Microsoft Entra app (tenant ID and client ID in config, client
secret encrypted in credentials) signs posts for a trigger that allows only
people in the tenant.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from typing import Any, Callable, Mapping
from urllib.parse import SplitResult, parse_qs, urlsplit

import httpx
from fastapi import HTTPException, status

from backend.auth.graph_oauth import (
    GraphOAuthError,
    acquire_app_only_token,
    forget_token,
)
from backend.auth.secrets import decrypt_secret
from backend.bots.delivery import DeliveryReceipt
from backend.bots.incident_card import event_headline, public_base_url
from backend.db.models import BotConnector
from backend.paging.teams_cards import (
    offered_actions,
    workflows_incident_body,
    workflows_text_body,
)
from .base import FieldSpec, InboundMessage

log = logging.getLogger(__name__)

PLATFORM = "teams_workflows"
# The one destination: the channel or chat the workflow posts to. A fixed,
# non-secret chat id, since the URL is never a chat id.
DESTINATION = "workflow"
# The Power Automate audience plus /.default, for an Entra app token.
FLOW_SCOPE = "https://service.flow.microsoft.com//.default"
KNOWN_HOST_SUFFIXES = (".logic.azure.com", ".powerplatform.com")
RETIRED_HOSTS = frozenset({"outlook.office.com", "outlook.office365.com"})
RETIRED_HOST_SUFFIX = ".webhook.office.com"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
LIVE_TEST_NOTE = (
    "Teams Workflows accepted the test card. Teams shows it once the workflow "
    "runs; if it does not appear within a minute, open the workflow's run "
    "history in Power Automate."
)

_ENCRYPTED_PREFIX = "enc:"


def _plain(value: object) -> str:
    text = str(value or "")
    if text.startswith(_ENCRYPTED_PREFIX):
        return decrypt_secret(text[len(_ENCRYPTED_PREFIX) :])
    return text


def _readable(value: object) -> str | None:
    """The stored value in plain text, or None when this server's key cannot
    decrypt it."""
    try:
        return _plain(value).strip()
    except (ValueError, RuntimeError):
        return None


def _https_parts(url: str) -> SplitResult | None:
    """The URL's parts when it is https with a host, else None."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:  # a malformed URL, such as an unclosed IPv6 bracket
        return None
    if parts.scheme != "https" or not host:
        return None
    return parts


def _failed(error: str, *, retryable: bool = False) -> DeliveryReceipt:
    return DeliveryReceipt(ok=False, error=error, retryable=retryable)


def _entra_failure(exc: GraphOAuthError) -> DeliveryReceipt:
    """Map a token failure without its message, which can echo the answer."""
    code = exc.status_code
    if code in {400, 401, 403}:
        return _failed(
            f"Microsoft Entra refused the app credentials (HTTP {code}): check "
            "the tenant ID, client ID and client secret"
        )
    if code is not None and (code in {408, 429} or code >= 500):
        return _failed(f"Microsoft Entra did not answer (HTTP {code})", retryable=True)
    if code is None and isinstance(exc.__cause__, httpx.HTTPError):
        return _failed(
            f"Microsoft Entra did not answer ({type(exc.__cause__).__name__})",
            retryable=True,
        )
    return _failed("Microsoft Entra sign-in failed")


def _answer(code: int) -> DeliveryReceipt:
    """Map the workflow trigger's HTTP answer."""
    if 200 <= code < 300:
        # Accepted: the workflow took the card; it may still fail inside the
        # flow, which only its run history shows.
        return DeliveryReceipt(
            ok=True,
            delivery_status="accepted",
            can_update=False,
            external_message_id=None,
            external_channel_id=DESTINATION,
        )
    if code == 429:
        return _failed("Teams Workflows is throttling posts (HTTP 429)", retryable=True)
    if code == 408 or code >= 500:
        return _failed(f"Teams Workflows did not answer (HTTP {code})", retryable=True)
    if 300 <= code < 400:
        return _failed(
            f"Teams Workflows answered with a redirect (HTTP {code}), which "
            "OpsMender does not follow"
        )
    if code == 400:
        return _failed("Teams Workflows refused the card (HTTP 400)")
    if code in {401, 403}:
        return _failed(
            f"Teams Workflows refused the caller (HTTP {code}): check who may "
            "trigger the workflow, or the Microsoft Entra sign-in"
        )
    if code == 404:
        return _failed(
            "Teams Workflows did not find the workflow (HTTP 404): it may be off "
            "or deleted, or its URL regenerated; paste the current URL"
        )
    if code == 413:
        return _failed("Teams Workflows refused the card as too large (HTTP 413)")
    return _failed(f"Teams Workflows error: HTTP {code}")


def _workflow_url_check(url: str | None) -> tuple[str, str, str]:
    if url is None:
        return (
            "workflow_url",
            "fail",
            "The saved workflow URL cannot be read with this server's secret key. "
            "Paste it again.",
        )
    parts = _https_parts(url)
    if parts is None:
        return ("workflow_url", "fail", "The workflow URL must start with https://.")
    host = parts.hostname or ""
    if host in RETIRED_HOSTS or host.endswith(RETIRED_HOST_SUFFIX):
        return (
            "workflow_url",
            "fail",
            "This is an Office 365 connector URL, which Microsoft retired. Create "
            "a workflow from the Send webhook alerts to a channel template and "
            "paste its URL.",
        )
    if host.endswith(KNOWN_HOST_SUFFIXES):
        return ("workflow_url", "pass", f"Posts go to the Power Automate host {host}.")
    return (
        "workflow_url",
        "warn",
        f"{host} is not a Power Automate host OpsMender knows. Check that you "
        "copied the URL from the workflow's trigger; government clouds use "
        "other hosts.",
    )


def _public_url_check() -> tuple[str, str, str]:
    raw = (os.environ.get("OPSMENDER_PUBLIC_URL") or "").strip()
    base = public_base_url()
    try:
        parts = urlsplit(raw)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if not raw or parts is None or not host or host in LOCAL_HOSTS:
        return (
            "public_url",
            "warn",
            f"OPSMENDER_PUBLIC_URL is not set to a public address, so card "
            f"buttons open {base}. Set it to the address people use to open "
            "OpsMender.",
        )
    if parts.scheme != "https":
        return ("public_url", "warn", f"Card buttons open {base} over plain http.")
    return ("public_url", "pass", f"Card buttons open {base}.")


def config_checks(connector: BotConnector) -> list[tuple[str, str, str]]:
    """Network-free Check configuration rows, as ``(name, level, detail)``.

    They name the workflow's host, which is not secret, and never its path,
    query or signature.
    """
    credentials = connector.credentials or {}
    config = connector.config or {}
    url = _readable(credentials.get("webhook_url"))
    url_check = _workflow_url_check(url)
    checks = [url_check]

    tenant = str(config.get("entra_tenant_id") or "").strip()
    client = str(config.get("entra_client_id") or "").strip()
    secret_stored = bool(str(credentials.get("entra_client_secret") or "").strip())
    entra_set = [bool(tenant), bool(client), secret_stored]

    if url_check[1] != "fail" and not any(entra_set):
        parts = _https_parts(url or "")
        sig = parse_qs(parts.query if parts is not None else "").get("sig", [])
        if not any(value.strip() for value in sig):
            checks.append(
                (
                    "workflow_signature",
                    "warn",
                    "The URL has no sig value. A workflow whose trigger allows "
                    "Anyone has one; if the trigger allows only people in your "
                    "tenant, fill in the Microsoft Entra fields.",
                )
            )

    if secret_stored and _readable(credentials.get("entra_client_secret")) is None:
        checks.append(
            (
                "entra",
                "fail",
                "The saved client secret cannot be read with this server's secret "
                "key. Enter it again.",
            )
        )
    elif not any(entra_set):
        checks.append(
            (
                "entra",
                "pass",
                "No Microsoft Entra sign-in: the workflow's trigger must allow Anyone.",
            )
        )
    elif all(entra_set):
        parts = _https_parts(url or "")
        trusted = parts is not None and (parts.hostname or "").lower().endswith(
            KNOWN_HOST_SUFFIXES
        )
        checks.append(
            (
                "entra",
                "pass" if trusted else "fail",
                (
                    "Posts carry a Microsoft Entra app token for a trigger limited to "
                    "your tenant."
                )
                if trusted
                else "Microsoft Entra sign-in requires a known Power Automate host",
            )
        )
    else:
        checks.append(
            (
                "entra",
                "fail",
                "Microsoft Entra sign-in needs the tenant ID, client ID and client "
                "secret together. Fill in all three or clear them.",
            )
        )

    checks.append(_public_url_check())
    return checks


def _current_app_config():
    # Imported here: backend.api.deps is not needed to load this adapter.
    from backend.api.deps import get_current_app_config

    return get_current_app_config()


class TeamsWorkflowsAdapter:
    platform = PLATFORM
    fixed_destination = DESTINATION
    # Microsoft allows 25 Flow bot posts per connection in 5 minutes.
    # OpsMender keeps 5 back for the owner's other flows on the same
    # connection and clock skew.
    posts_per_window = 20
    post_window = timedelta(minutes=5)
    live_test_note = LIVE_TEST_NOTE

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        config_loader: Callable[[], Any] | None = None,
    ) -> None:
        # A redirect is refused, never followed: the URL is the secret.
        self._factory = http_client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False
            )
        )
        self._config_loader = config_loader or _current_app_config

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="webhook_url",
                label="Workflow URL",
                kind="secret",
                group="credentials",
                required=True,
                placeholder="https://...invoke?api-version=...&sig=...",
                helper=(
                    "Copy it from the workflow's trigger, When a Teams webhook "
                    "request is received. Anyone with this URL can post to the "
                    "channel, so OpsMender stores it encrypted and never shows it "
                    "again."
                ),
                doc_url=(
                    "https://learn.microsoft.com/en-us/microsoftteams/platform/"
                    "webhooks-and-connectors/how-to/add-incoming-webhook"
                ),
            ),
            FieldSpec(
                name="entra_tenant_id",
                label="Microsoft Entra tenant ID (optional)",
                kind="text",
                group="config",
                required=False,
                helper=(
                    "Only for a workflow whose trigger allows people in your "
                    "tenant. Fill in all three Microsoft Entra fields, or none."
                ),
            ),
            FieldSpec(
                name="entra_client_id",
                label="Application (client) ID (optional)",
                kind="text",
                group="config",
                required=False,
            ),
            FieldSpec(
                name="entra_client_secret",
                label="Client secret (optional)",
                kind="secret",
                group="credentials",
                required=False,
                helper=(
                    "Saving a new secret replaces the stored credentials, so "
                    "paste the workflow URL again too."
                ),
            ),
        ]

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Teams Workflows channels are outbound-only",
        )

    def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None

    def inline_reply(self, chat_id: str, text: str) -> dict[str, Any] | None:
        return None

    def config_checks(self, connector: BotConnector) -> list[tuple[str, str, str]]:
        return config_checks(connector)

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        """Post ``text`` as a small card (Send live test)."""
        receipt = await self._post(
            connector, lambda: workflows_text_body(text, base_url=public_base_url())
        )
        return receipt.ok, receipt.error

    async def send_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        incident=None,
        links_ready: bool = False,
        facts: list[tuple[str, str]] | None = None,
        acknowledged_by: str | None = None,
        event_type: str | None = None,
        incident_id: uuid.UUID | None = None,
    ) -> DeliveryReceipt:
        """Post one card for an incident change. Without the incident (a
        collision notice, an escalation built before its commit) the
        pre-built text becomes the card."""
        base = public_base_url()
        if incident is None:
            return await self._post(
                connector,
                lambda: workflows_text_body(
                    text, base_url=base, incident_id=incident_id
                ),
            )

        def build() -> bytes:
            links = (
                self._mint_links(connector, incident, acknowledged_by)
                if links_ready
                else {}
            )
            return workflows_incident_body(
                incident,
                headline=event_headline(event_type),
                facts=facts,
                acknowledged_by=acknowledged_by,
                base_url=base,
                action_links=links,
                exhausted=event_type == "incident.escalation_exhausted",
            )

        return await self._post(connector, build)

    def _mint_links(
        self, connector: BotConnector, incident, acknowledged_by: str | None
    ) -> dict[str, str]:
        """One fresh signed link per button the card shows. A failure posts
        the card with View in OpsMender only."""
        try:
            # Imported here: action_links imports the action code, which
            # imports the notifier, which imports this package.
            from backend.bots.action_links import action_link

            config = self._config_loader()
            return {
                action: action_link(
                    config,
                    connector=connector,
                    incident_id=incident.id,
                    action=action,
                )
                for action in offered_actions(incident.status, acknowledged_by)
            }
        except Exception as exc:  # noqa: BLE001 - the card still posts
            log.warning("Teams Workflows card links not signed: %s", type(exc).__name__)
            return {}

    async def _post(
        self, connector: BotConnector, build_body: Callable[[], bytes]
    ) -> DeliveryReceipt:
        """POST one card. Never raises, so one channel cannot abort another
        channel's fan-out."""
        try:
            return await self._send(connector, build_body)
        except Exception as exc:  # noqa: BLE001
            log.warning("Teams Workflows post failed: %s", type(exc).__name__)
            return _failed(f"Teams Workflows error: {type(exc).__name__}")

    async def _send(
        self, connector: BotConnector, build_body: Callable[[], bytes]
    ) -> DeliveryReceipt:
        credentials = connector.credentials or {}
        config = connector.config or {}
        url = _readable(credentials.get("webhook_url"))
        if url is None:
            return _failed("The stored workflow URL cannot be read; enter it again.")
        if not url:
            return _failed("The Teams Workflows URL is not configured")
        parts = _https_parts(url)
        if parts is None:
            return _failed("The Teams Workflows URL must use https")

        tenant = str(config.get("entra_tenant_id") or "").strip()
        client_id = str(config.get("entra_client_id") or "").strip()
        secret = _readable(credentials.get("entra_client_secret"))
        if secret is None:
            return _failed(
                "The stored Microsoft Entra client secret cannot be read; enter it "
                "again."
            )
        entra = (tenant, client_id, secret)
        if any(entra) and not all(entra):
            return _failed(
                "Microsoft Entra sign-in needs the tenant ID, client ID and client "
                "secret together"
            )

        if all(entra) and not (parts.hostname or "").lower().endswith(
            KNOWN_HOST_SUFFIXES
        ):
            return _failed(
                "Microsoft Entra sign-in requires a known Power Automate host"
            )

        body = build_body()
        headers = {"Content-Type": "application/json"}
        if all(entra):
            try:
                token = await acquire_app_only_token(
                    tenant_id=tenant,
                    client_id=client_id,
                    client_secret=secret,
                    scope=FLOW_SCOPE,
                    http_client_factory=self._factory,
                )
            except GraphOAuthError as exc:
                return _entra_failure(exc)
            headers["Authorization"] = f"Bearer {token.access_token}"

        try:
            async with self._factory() as http:
                response = await http.post(url, content=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # The type only: the error text can carry the URL.
            return _failed(
                f"Teams Workflows did not answer ({type(exc).__name__})",
                retryable=True,
            )
        receipt = _answer(response.status_code)
        if response.status_code in {401, 403} and all(entra):
            # A refused token is not reused: the next post fetches a new one.
            forget_token(tenant, client_id, FLOW_SCOPE)
        return receipt
