"""Concrete delivery channels for the paging dispatcher (Sprint 35).

Each class implements the ``Channel`` protocol in ``backend.paging.dispatch``:
a single async ``send(recipient, subject, body) -> DeliveryAttempt`` method.

Channels never raise on transport failure — they return a ``DeliveryAttempt``
with ``status="failed"`` + an error string. The dispatcher records every
attempt as an ``incident_pages`` row.

* ``SlackDMChannel`` — Slack ``chat.postMessage``.
* ``TeamsDMChannel`` — Teams incoming-webhook style (Microsoft Graph is
  deferred to Sprint 37).
* ``EmailChannel`` — SMTP via stdlib smtplib, wrapped in ``asyncio.to_thread``.
* ``SMSChannel`` — Twilio Messages API.

All HTTP-based channels accept an optional ``http_client`` factory so tests
can inject ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Callable, ClassVar

import httpx

from .dispatch import DeliveryAttempt


HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


class SlackDMChannel:
    key: ClassVar[str] = "slack_dm"

    def __init__(
        self,
        *,
        bot_token: str,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._bot_token = bot_token
        self._factory = http_client_factory or _default_http_client

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt:
        text = f"*{subject}*\n{body}"
        payload: dict = {"channel": recipient, "text": text}
        if blocks:
            payload["blocks"] = blocks
        try:
            async with self._factory() as client:
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {self._bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return DeliveryAttempt(self.key, "failed", f"network: {exc}")
        if resp.status_code != 200:
            return DeliveryAttempt(
                self.key, "failed", f"http {resp.status_code}"
            )
        try:
            data = resp.json()
        except ValueError:
            return DeliveryAttempt(self.key, "failed", "invalid json")
        if not data.get("ok"):
            return DeliveryAttempt(
                self.key, "failed", f"slack: {data.get('error') or 'unknown'}"
            )
        return DeliveryAttempt(self.key, "sent")


class TeamsDMChannel:
    key: ClassVar[str] = "teams_dm"

    def __init__(
        self,
        *,
        webhook_url: str,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._webhook_url = webhook_url
        self._factory = http_client_factory or _default_http_client

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt:
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": subject,
            "title": subject,
            "text": f"{body}\n\n_to: {recipient}_",
        }
        try:
            async with self._factory() as client:
                resp = await client.post(self._webhook_url, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryAttempt(self.key, "failed", f"network: {exc}")
        if resp.status_code >= 400:
            return DeliveryAttempt(
                self.key, "failed", f"http {resp.status_code}"
            )
        return DeliveryAttempt(self.key, "sent")


class TeamsGraphDMChannel:
    """Sprint 37 step 2 — Teams DM via Microsoft Graph (app-only auth).

    Recipient must be a Teams chat id that OpsMender's Azure AD app has
    permission to post into (the operator typically pre-creates a chat
    that includes the OpsMender service principal as a member, and
    stores that chat id in the user's notification preferences). Falls
    back to the connector's ``default_chat_id`` upstream of this class —
    we only see the resolved id.

    Step 3 will replace the plain HTML body with an adaptive card
    attachment carrying ack / take / resolve actions.
    """

    key: ClassVar[str] = "teams_dm_graph"

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._factory = http_client_factory or _default_http_client

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt:
        from backend.auth.graph_oauth import (
            GraphOAuthError,
            acquire_app_only_token,
        )

        try:
            token = await acquire_app_only_token(
                tenant_id=self._tenant_id,
                client_id=self._client_id,
                client_secret=self._client_secret,
                http_client_factory=self._factory,
            )
        except GraphOAuthError as exc:
            return DeliveryAttempt(self.key, "failed", f"graph_oauth: {exc}")

        # HTML body — Graph chat messages support `contentType: "html"`.
        # Step 3 will switch to an adaptive card attachment.
        text_html = (
            f"<p><strong>{subject}</strong></p><p>{body}</p>"
        )
        payload: dict = {
            "body": {
                "contentType": "html",
                "content": text_html,
            }
        }

        try:
            async with self._factory() as client:
                resp = await client.post(
                    f"https://graph.microsoft.com/v1.0/chats/{recipient}/messages",
                    headers={
                        "Authorization": (
                            f"{token.token_type} {token.access_token}"
                        ),
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return DeliveryAttempt(self.key, "failed", f"network: {exc}")

        if resp.status_code not in (200, 201):
            try:
                err = (resp.json().get("error") or {}).get("code")
            except ValueError:
                err = None
            detail = err or f"http {resp.status_code}"
            return DeliveryAttempt(self.key, "failed", f"graph: {detail}")
        return DeliveryAttempt(self.key, "sent")


class EmailChannel:
    key: ClassVar[str] = "email"

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        from_addr: str = "opsmender@localhost",
        use_tls: bool = True,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
    ):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._from_addr = from_addr
        self._use_tls = use_tls
        self._smtp_factory = smtp_factory

    def _new_smtp(self) -> smtplib.SMTP:
        if self._smtp_factory is not None:
            return self._smtp_factory()
        return smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10)

    def _send_sync(self, *, recipient: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)
        smtp = self._new_smtp()
        try:
            if self._use_tls:
                smtp.starttls()
            if self._smtp_user and self._smtp_password:
                smtp.login(self._smtp_user, self._smtp_password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt:
        try:
            await asyncio.to_thread(
                self._send_sync,
                recipient=recipient,
                subject=subject,
                body=body,
            )
        except Exception as exc:  # noqa: BLE001
            return DeliveryAttempt(self.key, "failed", str(exc))
        return DeliveryAttempt(self.key, "sent")


class SMSChannel:
    key: ClassVar[str] = "sms"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._factory = http_client_factory or _default_http_client

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt:
        text = f"{subject}\n{body}"
        if len(text) > 1500:
            text = text[:1497] + "..."
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        try:
            async with self._factory() as client:
                resp = await client.post(
                    url,
                    auth=(self._sid, self._token),
                    data={"From": self._from, "To": recipient, "Body": text},
                )
        except httpx.HTTPError as exc:
            return DeliveryAttempt(self.key, "failed", f"network: {exc}")
        if resp.status_code >= 400:
            try:
                data = resp.json()
                detail = data.get("message") or data.get("error_message") or ""
            except ValueError:
                detail = ""
            msg = f"http {resp.status_code}"
            if detail:
                msg = f"{msg}: {detail}"
            return DeliveryAttempt(self.key, "failed", msg)
        return DeliveryAttempt(self.key, "sent")
