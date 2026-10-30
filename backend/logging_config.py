"""Central logging configuration for OpsMender AI.

Log verbosity is a **process-global** concern. The effective level is sourced
in this order (later wins):

  1. ``OPSMENDER_LOG_LEVEL`` env var / ``.env`` - applied at process start.
  2. The persisted ``logging_level`` runtime-config override saved from the
     dashboard Config page - applied at startup once the DB is reachable, and
     again **live** whenever an admin saves a new value.

Before this module existed, ``OPSMENDER_LOG_LEVEL`` was read into config but
never applied to Python's logging system or uvicorn, so the level was always
effectively INFO regardless of env var, UI, or restart.

``configure_logging`` is idempotent and safe to call repeatedly.
"""

from __future__ import annotations

import logging
import re

VALID_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

_DEFAULT_LEVEL = "INFO"

# uvicorn installs these loggers with their own handlers + ``propagate=False``.
# Setting their level explicitly is what makes the dashboard setting govern
# HTTP access logs (the "GET /config 200 OK" lines) too - not just OpsMender's
# own loggers.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# The HTTP client logs every request's full URL at INFO (and more at DEBUG).
# Some outbound URLs are credentials: a Slack response_url, a Discord
# interaction follow-up, a chat bot token in the path. These loggers never go
# below WARNING, whatever the level.
_HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


# A service's intake URL carries its secret in the path, and the access log
# prints paths (S-108). Keep the first eight characters, like the UI's hint.
_INTAKE_SECRET = re.compile(r"(/api/v1/intake/[^/?#\s\"]{0,8})[^/?#\s\"]*")

# A stream's bearer token used to travel in the URL (``?token=<JWT>``), and
# older clients or a proxy may still send it that way. The access log prints
# the request line with its query string, so blank the value of any token
# parameter before the record is formatted (F6).
_QUERY_TOKEN = re.compile(r"([?&](?:token|access_token)=)[^&\s\"']*")


def redact_intake_secrets(text: str) -> str:
    """Replace everything past a service intake token's first eight characters."""
    return _INTAKE_SECRET.sub(r"\g<1>…", text)


def redact_query_tokens(text: str) -> str:
    """Blank the value of a ``token`` or ``access_token`` query parameter."""
    return _QUERY_TOKEN.sub(r"\g<1>[redacted]", text)


def redact_access_secrets(text: str) -> str:
    """Every redaction the access log needs: intake paths and query tokens."""
    return redact_query_tokens(redact_intake_secrets(text))


class AccessSecretFilter(logging.Filter):
    """Redacts secrets from access-log records before they are formatted.

    uvicorn logs the request line through ``%s`` arguments (and a WebSocket
    handshake as ``"WebSocket %s"``), so both the message and every string
    argument are redacted.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_access_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_access_secrets(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


# The filter's earlier name, kept for callers that install or look for it.
IntakeSecretFilter = AccessSecretFilter


def normalize_level(level: str | None) -> str:
    """Return *level* upper-cased if valid, else the INFO default."""
    candidate = (level or "").strip().upper()
    return candidate if candidate in VALID_LEVELS else _DEFAULT_LEVEL


def configure_logging(level: str | None) -> str:
    """Apply *level* process-wide. Returns the normalized level applied.

    Configures the root logger (so OpsMender's own ``logging.getLogger(__name__)``
    output reaches stdout) plus uvicorn's pre-installed loggers, and keeps the
    HTTP client's loggers at WARNING or above so no outbound URL is logged.
    """
    normalized = normalize_level(level)
    numeric = getattr(logging, normalized)

    root = logging.getLogger()
    # uvicorn only installs handlers on its own loggers; without a root handler
    # OpsMender's app logs would be swallowed. Add one the first time only.
    if not root.handlers:
        logging.basicConfig(level=numeric)
    root.setLevel(numeric)

    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).setLevel(numeric)
    for name in _HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(max(numeric, logging.WARNING))

    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessSecretFilter) for f in access.filters):
        access.addFilter(AccessSecretFilter())

    return normalized
