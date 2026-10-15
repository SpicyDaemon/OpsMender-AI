"""Refuse deeply nested JSON request bodies before any route reads them (N-03).

A JSON body whose objects and arrays nest deeper than Python can walk raises
RecursionError wherever it is walked: while FastAPI encodes a 422 for it,
while a route saves it, or while a response that echoes it is serialised, so
it ends as a server error, sometimes after the value was stored. This ASGI
middleware reads JSON bodies first and answers 400 for one that nests more
than the limit, the same 64 levels the alert intake allows. The intake routes
are left alone (POST only, their one method): they cap the body size while
reading it (O-12) and check the depth themselves, with a row in the token's
delivery log. A client that leaves mid-body is passed on exactly as it was
received, so the route answers it as it always has.
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
        intake = scope.get("method") == "POST" and (
            scope["path"] == INTAKE_ROUTE or scope["path"].startswith(INTAKE_URL_PREFIX)
        )
        if (
            scope["type"] != "http"
            or scope["method"] not in _METHODS
            or intake
            or not _json_body(scope)
        ):
            await self.app(scope, receive, send)
            return
        messages: list[Message] = []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        if messages[-1]["type"] == "http.request":
            body = b"".join(m.get("body", b"") for m in messages)
            if body and nests_too_deeply(body, self.limit):
                response = JSONResponse(
                    status_code=400, content={"detail": "Request body nests too deeply"}
                )
                await response(scope, receive, send)
                return
            messages = [{"type": "http.request", "body": body, "more_body": False}]
        # Otherwise the client left mid-body: the route gets what arrived and
        # then the disconnect, as it would have without this check.
        pending = iter(messages)

        async def replay() -> Message:
            for message in pending:
                return message
            return await receive()

        await self.app(scope, replay, send)
