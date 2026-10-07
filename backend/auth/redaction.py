"""What Viewers may see of a configured endpoint (O-07).

Viewers and viewer tokens never receive credential values: a URL keeps its
scheme, host, port and path, and loses any user name, password, query string
and fragment, where tokens usually ride.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def url_without_secrets(url: str | None) -> str | None:
    """``url`` without user info, query string or fragment."""
    if not url:
        return url
    try:
        parts = urlsplit(str(url))
        host = parts.hostname or ""
        if ":" in host:  # an IPv6 literal keeps its brackets
            host = f"[{host}]"
        netloc = host if parts.port is None else f"{host}:{parts.port}"
    except ValueError:
        return None
    if not parts.scheme and not netloc:
        return None
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# The parts of a probe target's configuration a Viewer may see.
_PROBE_FIELDS = {
    "http": ("method", "timeout", "expected_status", "expected_statuses"),
    "tcp": ("host", "port", "timeout"),
}


def probe_config_for_viewer(kind: str, config: dict | None) -> dict | None:
    """A probe target's configuration without credentials: the URL loses its
    secrets and only fields that never carry one remain (no headers, bodies
    or auth settings)."""
    if not isinstance(config, dict):
        return config
    safe = {key: config[key] for key in _PROBE_FIELDS.get(kind, ()) if key in config}
    if "url" in config:
        safe["url"] = url_without_secrets(config.get("url"))
    return safe
