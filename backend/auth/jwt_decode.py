"""Decode a JSON Web Token so one nested too deeply to read is a bad token.

python-jose turns a header or claims set that is not valid JSON into a
JWTError, but one whose objects or arrays nest past Python's recursion limit
escapes as RecursionError, which would end the request as a server error
(N-03, EC-I05). Every place OpsMender decodes a token calls :func:`decode`, so
such a token is refused like any other bad one.
"""

from __future__ import annotations

from typing import Any

from jose import jwt
from jose.exceptions import JWTError


def decode(token: str, key: Any, **kwargs: Any) -> dict[str, Any]:
    """``jose.jwt.decode``, raising JWTError for a token too deeply nested to read."""
    try:
        return jwt.decode(token, key, **kwargs)
    except RecursionError as exc:
        raise JWTError("Token nests too deeply to read") from exc
