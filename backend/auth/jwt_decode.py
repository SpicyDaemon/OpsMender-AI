"""Decode a JSON Web Token so a bad one is refused cleanly.

python-jose turns a header or claims set that is not valid JSON into a
JWTError, but one whose objects or arrays nest past Python's recursion limit
escapes as RecursionError, which would end the request as a server error
(N-03, EC-I05). Every place OpsMender decodes a token calls :func:`decode`, so
such a token is refused like any other bad one.

python-jose also builds every key of a key set for the token's algorithm, in
order, and stops with an error at the first one that does not fit, so an
identity provider that publishes an EC key before its RSA keys would make a
valid RS256 id token fail (M1-58). decode tries only the keys that can verify
the token, and only those with its key id when any key has it, so a key set's
order and mix of key types no longer matter. A provider that publishes one
bare key instead of a key set gets the same treatment, so a bare key that
does not fit is a refusal, not a server error (M1-65).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jose import jwk, jwt
from jose.exceptions import JWTError


def _usable_keys(token: str, key: Any) -> Any:
    """From a key set, the keys that can verify ``token``, built for its
    algorithm: those with its key id when any has it. Any other key is
    returned as given."""
    if isinstance(key, Mapping) and "keys" not in key and "kty" in key:
        key = {"keys": [key]}
    if not (isinstance(key, Mapping) and isinstance(key.get("keys"), list)):
        return key
    header = jwt.get_unverified_header(token)
    algorithm, kid = header.get("alg"), header.get("kid")
    keys = [entry for entry in key["keys"] if isinstance(entry, Mapping)]
    if kid is not None and any(entry.get("kid") == kid for entry in keys):
        keys = [entry for entry in keys if entry.get("kid") == kid]
    usable = []
    for entry in keys:
        try:
            usable.append(jwk.construct(dict(entry), algorithm))
        except Exception:  # noqa: BLE001 - a key that cannot verify this algorithm
            continue
    return {"keys": usable}


def decode(token: str, key: Any, **kwargs: Any) -> dict[str, Any]:
    """``jose.jwt.decode`` that refuses a token too deeply nested to read and
    tries only the keys of a key set that can verify the token."""
    try:
        return jwt.decode(token, _usable_keys(token, key), **kwargs)
    except RecursionError as exc:
        raise JWTError("Token nests too deeply to read") from exc
