"""Capability-scoped integration adapter contract."""

from __future__ import annotations

import abc
import dataclasses
import inspect
import re
import types
import typing
from typing import Any, Literal

from backend.db.models import IntegrationConnector


IntegrationFieldKind = Literal[
    "text",
    "secret",
    "url",
    "number",
    "select",
    "textarea",
]
IntegrationFieldGroup = Literal["credentials", "config"]


@dataclasses.dataclass(frozen=True)
class IntegrationFieldSpec:
    """Typed operator-facing field stored in an integration JSON bag."""

    name: str
    label: str
    kind: IntegrationFieldKind = "text"
    group: IntegrationFieldGroup = "config"
    required: bool = False
    helper: str | None = None
    placeholder: str | None = None
    doc_url: str | None = None
    options: tuple[tuple[str, str], ...] = ()
    default: Any = None


@dataclasses.dataclass(frozen=True)
class IntegrationCapability:
    action: str
    description: str
    classification: str = "safe"
    mutating: bool = False
    always_requires_approval: bool = False


@dataclasses.dataclass(frozen=True)
class IntegrationArgument:
    """One argument of an integration action, as the model must send it.

    ``default`` is what the action uses when the argument is left out and
    that is worth telling the model: the connector's configured value (its
    namespace, project, job) or the adapter's own fallback.
    """

    name: str
    type: str
    required: bool
    default: str | None = None

    def describe(self) -> str:
        flags = [self.type]
        if self.required:
            flags.append("required")
        elif self.default:
            flags.append(f'default "{self.default}"')
        return f"{self.name} ({', '.join(flags)})"


_SCALAR_LABELS: dict[Any, str] = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
}
_INTEGER_TEXT = re.compile(r"[+-]?[0-9]+")


def _type_label(hint: Any) -> str:
    """A JSON-style name for an annotation: ``str | None`` reads "string"
    and ``list[int]`` reads "array of integer"."""
    if hint is inspect.Parameter.empty or hint is Any:
        return "any"
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        labels: list[str] = []
        for member in typing.get_args(hint):
            if member is type(None):
                continue
            label = _type_label(member)
            if label not in labels:
                labels.append(label)
        return "/".join(labels) or "any"
    if origin in (list, tuple) or hint in (list, tuple):
        members = [m for m in typing.get_args(hint) if m is not Ellipsis]
        element = _type_label(members[0]) if members else "any"
        return "array" if "any" in element or "/" in element else f"array of {element}"
    if origin is dict or hint is dict:
        return "object"
    return _SCALAR_LABELS.get(hint, "any")


def _value_matches(label: str, value: Any) -> bool:
    """Whether a JSON value fits one type label.

    Strict where a loose match changes what runs: only a real boolean is a
    boolean (the text "false" is truthy), and an object or a list is never a
    scalar. Lenient where the adapter converts anyway: a number may stand for
    a string identifier, and a whole number written as 5.0 or as text for an
    integer.
    """
    if label == "any":
        return True
    if label == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if label == "string":
        return isinstance(value, (str, int, float))
    if label == "integer":
        if isinstance(value, float):
            return value.is_integer()
        return isinstance(value, int) or (
            isinstance(value, str) and bool(_INTEGER_TEXT.fullmatch(value.strip()))
        )
    if label == "number":
        return isinstance(value, (int, float))
    if label == "object":
        return isinstance(value, dict)
    if label == "array":
        return isinstance(value, (list, tuple))
    if label.startswith("array of "):
        element = label.removeprefix("array of ")
        return isinstance(value, (list, tuple)) and all(
            _value_matches(element, item) for item in value
        )
    return True


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def describe_arguments(arguments: tuple[IntegrationArgument, ...]) -> str:
    """The argument list as the plan prompt shows it, like an MCP tool's."""
    if not arguments:
        return "Arguments: none."
    return "Arguments: " + ", ".join(item.describe() for item in arguments) + "."


@dataclasses.dataclass(frozen=True)
class IntegrationResult:
    ok: bool
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, **data: Any) -> "IntegrationResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> "IntegrationResult":
        return cls(ok=False, error=error)


class IntegrationAdapter(abc.ABC):
    kind: str
    capabilities: tuple[IntegrationCapability, ...] = ()
    # What an argument falls back to when neither the call nor the
    # connector's configuration names it (Kubernetes' "default" namespace).
    argument_fallbacks: typing.ClassVar[dict[str, str]] = {}

    @abc.abstractmethod
    async def test_connection(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> IntegrationResult:
        """Perform a cheap authenticated probe."""

    def argument_alternatives(
        self, connector: IntegrationConnector, action: str
    ) -> frozenset[str]:
        """Configuration-backed arguments this action can do without.

        For an adapter where one of two arguments is enough, where the
        edition decides which of them is used, or where an action treats
        the configured value as a filter rather than a target.
        """
        return frozenset()

    def arguments_for(
        self, action: str, connector: IntegrationConnector | None = None
    ) -> tuple[IntegrationArgument, ...]:
        """The argument contract of one action, read from its method.

        The method signature is the source: the names are the keyword
        arguments the call is made with, an argument without a default is
        required, and the annotation gives the type. With a connector, an
        argument that falls back to the connector's configuration carries
        that value as its default, and is required when the connector has
        none: "leave it out" is only true when there is something to fall
        back to. The planner is shown this contract and ``invoke`` checks a
        call against it (M1-138).
        """
        if action == "test_connection":
            return ()
        method = getattr(self, action, None)
        if not callable(method):
            return ()
        try:
            hints = typing.get_type_hints(method)
        except Exception:  # noqa: BLE001 - an unresolvable hint reads "any"
            hints = {}
        configured: set[str] = set()
        alternatives: frozenset[str] = frozenset()
        if connector is not None:
            from backend.integrations.registry import config_fields

            configured = {field.name for field in config_fields(self.kind)}
            configured |= set(self.argument_fallbacks)
            alternatives = self.argument_alternatives(connector, action)
        arguments: list[IntegrationArgument] = []
        for parameter in list(inspect.signature(method).parameters.values())[2:]:
            if parameter.kind in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                continue
            required = parameter.default is inspect.Parameter.empty
            default = None
            if parameter.default is None and parameter.name in configured:
                default = self._configured_default(connector, parameter.name)
                if default is None and parameter.name not in alternatives:
                    required = True
            arguments.append(
                IntegrationArgument(
                    name=parameter.name,
                    type=_type_label(hints.get(parameter.name, parameter.annotation)),
                    required=required,
                    default=default,
                )
            )
        return tuple(arguments)

    def _configured_default(
        self, connector: IntegrationConnector | None, name: str
    ) -> str | None:
        value = (connector.config or {}).get(name) if connector is not None else None
        text = "" if isinstance(value, (dict, list, bool)) else str(value or "")
        text = " ".join(text.split()) or self.argument_fallbacks.get(name, "")
        return text[:80] or None

    def check_arguments(
        self,
        action: str,
        parameters: Any,
        connector: IntegrationConnector | None = None,
    ) -> str | None:
        """Why a call does not fit the action's contract, or ``None``.

        Checked before the adapter runs, so a wrong argument never reaches
        the provider and the caller is told the names that do exist.
        """
        arguments = self.arguments_for(action, connector)
        contract = describe_arguments(arguments)
        label = f"{self.kind}.{action}"
        if not isinstance(parameters, dict):
            return f"{label} takes its arguments as an object. {contract}"
        known = {item.name: item for item in arguments}
        unknown = sorted(str(name) for name in parameters if name not in known)
        if unknown:
            # A caller's own words come back short: the names are for the
            # next attempt, not a place to park text.
            shown = ", ".join(f"'{name[:60]}'" for name in unknown[:5])
            more = f" and {len(unknown) - 5} more" if len(unknown) > 5 else ""
            noun = "argument" if len(unknown) == 1 else "arguments"
            return f"{label} does not take the {noun} {shown}{more}. {contract}"
        missing = [
            item.name
            for item in arguments
            if item.required and parameters.get(item.name) is None
        ]
        if missing:
            names = ", ".join(f"'{name}'" for name in missing)
            noun = "argument" if len(missing) == 1 else "arguments"
            return f"{label} needs the {noun} {names}. {contract}"
        for name, value in parameters.items():
            if value is None:
                continue
            expected = known[name].type
            if not any(_value_matches(part, value) for part in expected.split("/")):
                return (
                    f"{label}: the argument '{name}' must be {expected}, "
                    f"not {_json_type(value)}. {contract}"
                )
        return None

    async def invoke(
        self,
        action: str,
        connector: IntegrationConnector,
        auth: dict[str, Any],
        parameters: dict[str, Any],
    ) -> IntegrationResult:
        if action == "test_connection":
            return await self.test_connection(connector, auth)
        method = getattr(self, action, None)
        if not callable(method):
            return IntegrationResult.failure(
                f"Action '{action}' is not implemented for {self.kind}"
            )
        problem = self.check_arguments(action, parameters, connector)
        if problem:
            return IntegrationResult.failure(problem)
        types_by_name = {
            item.name: item.type for item in self.arguments_for(action, connector)
        }
        call: dict[str, Any] = {}
        for name, value in parameters.items():
            if value is None:
                # Null is "left out": it must not replace a default such as
                # a private note's private=True.
                continue
            if isinstance(value, float) and types_by_name.get(name) == "integer":
                value = int(value)
            call[name] = value
        return await method(connector, auth, **call)

    async def safe_invoke(
        self,
        action: str,
        connector: IntegrationConnector,
        auth: dict[str, Any],
        parameters: dict[str, Any] | None = None,
    ) -> IntegrationResult:
        try:
            result = await self.invoke(action, connector, auth, parameters or {})
        except Exception as exc:  # noqa: BLE001
            return IntegrationResult.failure(str(exc))
        if not isinstance(result, IntegrationResult):
            return IntegrationResult.failure(
                f"{self.kind}.{action} returned an invalid result"
            )
        return result
