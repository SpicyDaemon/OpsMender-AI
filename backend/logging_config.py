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


# A service's intake URL carries its secret in the path, and the access log
# prints paths (S-108). Keep the first eight characters, like the UI's hint.
_INTAKE_SECRET = re.compile(r"(/api/v1/intake/[^/?#\s\"]{0,8})[^/?#\s\"]*")


def redact_intake_secrets(text: str) -> str:
    """Replace everything past a service intake token's first eight characters."""
    return _INTAKE_SECRET.sub(r"\g<1>…", text)


class IntakeSecretFilter(logging.Filter):
    """Redacts intake secrets from log records before they are formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "/api/v1/intake/" in record.msg:
            record.msg = redact_intake_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_intake_secrets(arg)
                if isinstance(arg, str) and "/api/v1/intake/" in arg
                else arg
                for arg in record.args
            )
        return True


def normalize_level(level: str | None) -> str:
    """Return *level* upper-cased if valid, else the INFO default."""
    candidate = (level or "").strip().upper()
    return candidate if candidate in VALID_LEVELS else _DEFAULT_LEVEL


def configure_logging(level: str | None) -> str:
    """Apply *level* process-wide. Returns the normalized level applied.

    Configures the root logger (so OpsMender's own ``logging.getLogger(__name__)``
    output reaches stdout) plus uvicorn's pre-installed loggers.
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

    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, IntakeSecretFilter) for f in access.filters):
        access.addFilter(IntakeSecretFilter())

    return normalized
