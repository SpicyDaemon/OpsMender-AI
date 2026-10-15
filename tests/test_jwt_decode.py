"""M1-58: from a key set, decode tries only the keys that can verify the token.

python-jose builds every key of a key set for the token's algorithm and stops
with an error at the first one that does not fit, so a key set that mixes key
types, or holds a key that cannot be built, refused a valid token. Every
signature here is valid unless a test says otherwise.
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jose import jwk
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

from backend.auth.jwt_decode import decode


@pytest.fixture(scope="module")
def keys():
    rsa_a = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_b = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ec_key = ec.generate_private_key(ec.SECP256R1())

    def public(key, algorithm, kid):
        return {**jwk.construct(key, algorithm).public_key().to_dict(), "kid": kid}

    return {
        "rsa_a": rsa_a,
        "rsa_b": rsa_b,
        "public_rsa_a": public(rsa_a, "RS256", "a"),
        "public_rsa_b": public(rsa_b, "RS256", "b"),
        "public_ec": public(ec_key, "ES256", "ec"),
    }


def _token(key, kid=None):
    now = int(time.time())
    headers = {"kid": kid} if kid else None
    return jose_jwt.encode(
        {"sub": "someone", "iat": now, "exp": now + 300},
        key,
        algorithm="RS256",
        headers=headers,
    )


def test_a_key_set_that_mixes_key_types_still_verifies(keys):
    key_set = {"keys": [keys["public_ec"], keys["public_rsa_a"]]}
    claims = decode(_token(keys["rsa_a"]), key_set, algorithms=["RS256"])
    assert claims["sub"] == "someone"


def test_a_key_that_cannot_be_built_is_skipped(keys):
    broken = {"kty": "RSA", "kid": "broken", "n": "", "e": "AQAB"}
    key_set = {"keys": [broken, keys["public_rsa_a"]]}
    claims = decode(_token(keys["rsa_a"]), key_set, algorithms=["RS256"])
    assert claims["sub"] == "someone"


def test_the_token_key_id_picks_its_key(keys):
    key_set = {"keys": [keys["public_rsa_a"], keys["public_rsa_b"]]}
    claims = decode(_token(keys["rsa_b"], kid="b"), key_set, algorithms=["RS256"])
    assert claims["sub"] == "someone"
    # Signed by key a but naming key b: only key b is tried, so it fails.
    with pytest.raises(JWTError):
        decode(_token(keys["rsa_a"], kid="b"), key_set, algorithms=["RS256"])


def test_no_usable_key_is_a_clean_refusal(keys):
    with pytest.raises(JWTError):
        decode(
            _token(keys["rsa_a"]), {"keys": [keys["public_ec"]]}, algorithms=["RS256"]
        )
