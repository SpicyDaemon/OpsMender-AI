"""Capability-scoped integration adapter contract."""

from __future__ import annotations

import abc
import dataclasses
from typing import Any

from backend.db.models import IntegrationConnector


@dataclasses.dataclass(frozen=True)
class IntegrationCapability:
    action: str
    description: str
    classification: str = "safe"
    mutating: bool = False
    always_requires_approval: bool = False


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

    @abc.abstractmethod
    async def test_connection(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> IntegrationResult:
        """Perform a cheap authenticated probe."""

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
        return await method(connector, auth, **parameters)

    async def safe_invoke(
        self,
        action: str,
        connector: IntegrationConnector,
        auth: dict[str, Any],
        parameters: dict[str, Any] | None = None,
    ) -> IntegrationResult:
        try:
            result = await self.invoke(
                action, connector, auth, parameters or {}
            )
        except Exception as exc:  # noqa: BLE001
            return IntegrationResult.failure(str(exc))
        if not isinstance(result, IntegrationResult):
            return IntegrationResult.failure(
                f"{self.kind}.{action} returned an invalid result"
            )
        return result
