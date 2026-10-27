"""Concrete delivery channels for the paging dispatcher (Sprint 35).

Each class implements the ``Channel`` protocol in ``backend.paging.dispatch``:
a single async ``send(recipient, subject, body) -> DeliveryAttempt`` method.

Channels never raise on transport failure - they return a ``DeliveryAttempt``
with ``status="failed"`` + an error string. The dispatcher records every
attempt as an ``incident_pages`` row.

* ``SlackDMChannel`` - Slack ``chat.postMessage``.
* ``EmailChannel`` - SMTP via stdlib smtplib, wrapped in ``asyncio.to_thread``.
* ``SMSChannel`` - Twilio Messages API.

All HTTP-based channels accept an optional ``http_client`` factory so tests
can inject ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Callable, ClassVar
from xml.sax.saxutils import escape as _xml_escape

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
            return DeliveryAttempt(self.key, "failed", f"http {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return DeliveryAttempt(self.key, "failed", "invalid json")
        if not data.get("ok"):
            return DeliveryAttempt(
                self.key, "failed", f"slack: {data.get('error') or 'unknown'}"
            )
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
        from_name: str | None = None,
        security: str | None = None,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
    ):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._from_addr = from_addr
        self._from_name = from_name
        self._security = security or ("starttls" if use_tls else "none")
        self._smtp_factory = smtp_factory

    def _new_smtp(self) -> smtplib.SMTP:
        if self._smtp_factory is not None:
            return self._smtp_factory()
        client_type = smtplib.SMTP_SSL if self._security == "ssl" else smtplib.SMTP
        return client_type(self._smtp_host, self._smtp_port, timeout=10)

    def _send_sync(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachment: bytes | None = None,
        attachment_name: str | None = None,
        attachment_subtype: str = "octet-stream",
    ) -> None:
        msg = EmailMessage()
        msg["From"] = (
            f"{self._from_name} <{self._from_addr}>"
            if self._from_name
            else self._from_addr
        )
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)
        if attachment is not None:
            msg.add_attachment(
                attachment,
                maintype="application",
                subtype=attachment_subtype,
                filename=attachment_name or "report.bin",
            )
        smtp = self._new_smtp()
        try:
            if self._security == "starttls":
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

    async def send_with_attachment(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachment: bytes,
        attachment_name: str,
        attachment_subtype: str,
    ) -> DeliveryAttempt:
        try:
            await asyncio.to_thread(
                self._send_sync,
                recipient=recipient,
                subject=subject,
                body=body,
                attachment=attachment,
                attachment_name=attachment_name,
                attachment_subtype=attachment_subtype,
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


class VoiceChannel:
    """Outbound **phone call** personal-routing medium.

    A capability-gated voice provider (today: Twilio Programmable Voice). The
    abstract :class:`Channel` protocol is the telephony abstraction layer - a
    new voice provider plugs in as another ``key="voice"`` implementation behind
    ``channel_factory`` + the ``voice_call`` capability gate; chat bots that
    cannot place PSTN calls never expose this channel.

    Places an outbound call that speaks the page via inline TwiML ``<Say>``. An
    optional ``status_callback_url`` is forwarded to the provider, but OpsMender
    has no receiving route for it. Answering a call is not an acknowledgement;
    the responder must use the signed keypad action to acknowledge the incident.
    """

    key: ClassVar[str] = "voice"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        status_callback_url: str | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._status_callback_url = status_callback_url
        self._factory = http_client_factory or _default_http_client

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
        summary: str | None = None,
        ack_url: str | None = None,
    ) -> DeliveryAttempt:
        if ack_url and summary:
            # Speak a concise summary, then gather a keypad digit (1 = ack,
            # 2 = escalate, 3 = resolve, * = repeat) POSTed to the signed ack
            # callback.
            from backend.paging.page_text import format_voice_menu_twiml

            twiml = format_voice_menu_twiml(summary, ack_url)
        else:
            spoken = summary or f"{subject}. {body}"
            if len(spoken) > 900:
                spoken = spoken[:897] + "..."
            twiml = f"<Response><Say>{_xml_escape(spoken)}</Say></Response>"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Calls.json"
        data = {"From": self._from, "To": recipient, "Twiml": twiml}
        if self._status_callback_url:
            data["StatusCallback"] = self._status_callback_url
            data["StatusCallbackEvent"] = "answered"
        try:
            async with self._factory() as client:
                resp = await client.post(url, auth=(self._sid, self._token), data=data)
        except httpx.HTTPError as exc:
            return DeliveryAttempt(self.key, "failed", f"network: {exc}")
        if resp.status_code >= 400:
            try:
                payload = resp.json()
                detail = payload.get("message") or payload.get("error_message") or ""
            except ValueError:
                detail = ""
            msg = f"http {resp.status_code}"
            if detail:
                msg = f"{msg}: {detail}"
            return DeliveryAttempt(self.key, "failed", msg)
        return DeliveryAttempt(self.key, "sent")
