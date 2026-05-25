"""Best-effort outbound SMTP for invite + password-reset emails.

Sprint 56 ships SMTP as **secondary** delivery — the copy-paste URL
returned by the route is always the source of truth. SMTP failures log
a warning and return False; routes never raise.

Plain text only. No template engine. The caller passes ``subject`` and
``body`` already-formatted.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from backend.config_loader import SMTPConfig

logger = logging.getLogger(__name__)


def is_configured(cfg: SMTPConfig) -> bool:
    return cfg.configured


def send_email(
    cfg: SMTPConfig,
    *,
    to: str,
    subject: str,
    body: str,
) -> tuple[bool, str | None]:
    """Send ``body`` to ``to``. Returns ``(sent, error)``.

    Never raises. On any failure returns ``(False, "<reason>")`` and
    logs a warning so operators can find the cause in the audit trail.
    """

    if not cfg.configured:
        return False, "SMTP not configured"
    if not to:
        return False, "Empty recipient"

    msg = EmailMessage()
    msg["From"] = cfg.from_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=10) as client:
            if cfg.use_tls:
                client.starttls()
            if cfg.user and cfg.password:
                client.login(cfg.user, cfg.password)
            client.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("SMTP send to %s failed: %s", to, exc)
        return False, str(exc)
    return True, None
