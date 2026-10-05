"""The role a request acts with.

A signed-in user acts with their account role. A named API token acts with the
lower of its own role and its creator's current role, so a token never grants
more than its role and stops granting more than its creator once the creator is
demoted. Downstream permission checks must use :func:`request_role`, never
``user.role``: for a token request ``user`` is the creator's account row.
"""

from __future__ import annotations

from typing import Any

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def lower_role(first: str, second: str) -> str:
    """The less privileged of two roles. An unknown role ranks lowest."""

    return first if ROLE_RANK.get(first, -1) <= ROLE_RANK.get(second, -1) else second


def request_role(user: Any) -> str:
    """The role the current request acts with (see the module docstring).

    Falls back to the account role only when no request role was set, never
    when a token's role is set but unusable, so a bad value fails closed.
    """

    role = getattr(user, "effective_role", None)
    return user.role if role is None else role
