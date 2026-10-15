"""A model's key setting names an environment variable; it never holds a key.

`api_key_env_var` tells OpsMender which variable to read the provider key
from. A value that is not shaped like a variable name is most likely a pasted
key, so it is refused before it is read, stored or shown to anyone, and the
refusal never repeats it (M1-60).
"""

from __future__ import annotations

import re

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

KEY_NAME_MESSAGE = (
    "api_key_env_var must name an environment variable (letters, digits and "
    "underscores, not starting with a digit), not hold the key itself"
)


def check_key_env_var_name(value: str | None) -> None:
    """Raise ValueError, without repeating ``value``, unless it names a variable."""
    if value and not _NAME.fullmatch(value):
        raise ValueError(KEY_NAME_MESSAGE)
