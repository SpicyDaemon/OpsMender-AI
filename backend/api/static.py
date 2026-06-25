"""Serve the Next.js static export from FastAPI.

The frontend is built with ``output: 'export'`` which produces one HTML file
per route under ``frontend/out/``. This module registers a catch-all GET
handler that resolves incoming paths to those files:

* ``/`` → ``index.html``
* ``/login`` → ``login.html``
* ``/dashboard/incidents`` → ``dashboard/incidents.html``
* ``/dashboard/incidents/detail`` → ``dashboard/incidents/detail.html``
* ``/_next/static/...`` → the matching chunk file
* anything unresolved → ``404.html`` with a 404 status

The handler is registered AFTER the API routers, so specific API routes
(``/auth/login``, ``/incidents``, ``/ws/sessions/{id}/stream``, etc.) win on
match. Any GET request that isn't claimed by an API route falls through here.
"""

from __future__ import annotations

import logging
import pathlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

_LOG = logging.getLogger(__name__)


def mount_frontend(app: FastAPI, static_dir: pathlib.Path | str) -> None:
    """Register the catch-all handler if ``static_dir`` exists.

    Silently skips registration in dev mode where the frontend is served
    separately (e.g. ``next dev`` on :3000).
    """
    root = pathlib.Path(static_dir).resolve()
    if not root.is_dir():
        _LOG.info(
            "frontend static dir not found at %s — skipping SPA mount (dev mode?)",
            root,
        )
        return

    index_file = root / "index.html"
    not_found_file = root / "404.html"

    # GET + HEAD: HEAD requests come from health checkers, link previewers,
    # and prefetchers. FastAPI doesn't auto-derive HEAD from GET, so register
    # both explicitly.
    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    async def _serve_frontend(full_path: str, request: Request):  # noqa: ARG001
        safe_path = full_path.lstrip("/")
        # Reject traversal attempts up-front.
        if ".." in safe_path.split("/"):
            raise HTTPException(status_code=400, detail="invalid path")
        parts = safe_path.split("/")
        # Path-based org scoping: /org/<slug>/dashboard/... serves the same SPA
        # page as /dashboard/... (the org is resolved from the X-Org-ID header,
        # not the URL). Strip the /org/<slug> prefix to find the static file.
        # The legacy /o/<slug> prefix is also accepted so old bookmarks still
        # load (the client then rewrites them to /org/<slug>).
        if len(parts) >= 3 and parts[0] in ("org", "o") and parts[2] == "dashboard":
            safe_path = "/".join(parts[2:])

        candidates: list[pathlib.Path] = []
        if not safe_path:
            candidates.append(index_file)
        else:
            candidates.append(root / safe_path)
            candidates.append(root / f"{safe_path}.html")
            candidates.append(root / safe_path / "index.html")
            # Next.js 16 RSC prefetch payload rewrite. Requests like
            #   /dashboard/organizations/__next.dashboard.organizations.__PAGE__.txt
            # actually map to the nested file
            #   /dashboard/organizations/__next.dashboard/organizations/__PAGE__.txt
            # i.e. the first dot after `__next` is part of the directory name
            # `__next.<first>`; all subsequent dots in the basename are
            # directory separators. Add the rewritten path as a fallback
            # candidate so these prefetch requests stop 404'ing.
            candidate_path = pathlib.Path(safe_path)
            base = candidate_path.name
            if base.startswith("__next.") and base.endswith(".txt"):
                stem = base[: -len(".txt")]
                parts = stem.split(".")
                if len(parts) >= 3:
                    rewritten_basename = (
                        f"{parts[0]}.{parts[1]}/" + "/".join(parts[2:]) + ".txt"
                    )
                    candidates.append(root / candidate_path.parent / rewritten_basename)

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            # Ensure we never escape the static root via symlinks.
            if root not in resolved.parents and resolved != root:
                continue
            if resolved.is_file():
                return FileResponse(resolved)

        if not_found_file.is_file():
            return FileResponse(not_found_file, status_code=404)
        raise HTTPException(status_code=404, detail="not found")

    _LOG.info("frontend mounted from %s", root)
