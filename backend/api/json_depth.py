"""Refuse deeply nested JSON request bodies before any route reads them (N-03).

A JSON body whose objects and arrays nest deeper than Python can walk raises
RecursionError wherever it is walked: while FastAPI encodes a 422 for it,
while a route saves it, or while a response that echoes it is serialised, so
it ends as a server error, sometimes after the value was stored. This ASGI
middleware reads JSON bodies first and answers 400 for one that nests more
than the limit, the same 64 levels the alert intake allows. The intake routes
are left alone: they cap the body size while reading it (O-12) and check the
depth themselves, with a row in the token's delivery log.
"""

from __future__ import annotations

import json

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.ingest.adapters.universal import _MAX_JSON_DEPTH, _exceeds_depth

MAX_JSON_DEPTH = _MAX_JSON_DEPTH
INTAKE_ROUTE = "/incidents/ingest"
INTAKE_URL_PREFIX = "/api/v1/intake/"
_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def nests_too_deeply(body: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """True when ``body`` is JSON nested more than ``limit`` levels, or so deep
    that the JSON parser itself gives up. Anything else, invalid JSON included,
    is left for the route to answer as it always has."""
    try:
        value = json.loads(body)
    except RecursionError:
        return True
    except ValueError:
        return False
    return _exceeds_depth(value, limit)


def _json_body(scope: Scope) -> bool:
    """A request FastAPI would parse as JSON: a JSON content type, or none."""
    for name, value in scope.get("headers", ()):
        if name == b"content-type":
            return b"json" in value.lower()
    return True


class JsonDepthLimit:
    def __init__(self, app: ASGIApp, *, limit: int = MAX_JSON_DEPTH) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in _METHODS
            or scope["path"] == INTAKE_ROUTE
            or scope["path"].startswith(INTAKE_URL_PREFIX)
            or not _json_body(scope)
        ):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client left before sending the whole body
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        if body and nests_too_deeply(body, self.limit):
            response = JSONResponse(
                status_code=400, content={"detail": "Request body nests too deeply"}
            )
            await response(scope, receive, send)
            return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
