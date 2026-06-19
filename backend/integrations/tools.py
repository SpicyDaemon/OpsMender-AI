"""Internal integration tools bridged into the existing tier/audit pipeline."""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any

from mcp.types import CallToolResult, TextContent

from backend.db.repos import IntegrationConnectorRepo
from backend.integrations.base import IntegrationCapability
from backend.integrations.registry import get_adapter
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)


@dataclasses.dataclass(frozen=True)
class IntegrationToolDescriptor:
    name: str
    description: str
    connector_id: uuid.UUID
    capability: IntegrationCapability


def _tool_name(kind: str, action: str, connector_id: uuid.UUID) -> str:
    return f"integration__{kind}__{action}__{connector_id.hex}"


def _operation_for(
    descriptor: IntegrationToolDescriptor,
) -> OperationClassification:
    capability = descriptor.capability
    tiers = {
        0: OperationTierPolicy(
            enabled=not capability.mutating
            and not capability.always_requires_approval,
            mode=(
                "autonomous"
                if not capability.mutating
                and not capability.always_requires_approval
                else "blocked"
            ),
            require_reversible=False,
        ),
        1: OperationTierPolicy(
            enabled=True,
            mode=(
                "approval"
                if capability.mutating
                or capability.always_requires_approval
                else "autonomous"
            ),
        ),
        2: OperationTierPolicy(enabled=False, mode="advisory"),
    }
    return OperationClassification(
        tool=descriptor.name,
        classification=capability.classification,
        notes=descriptor.description,
        reversible=not capability.mutating,
        tiers=tiers,
    )


def merge_integration_skill(
    base: SkillDefinition,
    descriptors: list[IntegrationToolDescriptor],
) -> SkillDefinition:
    names = {operation.tool for operation in base.operations}
    integration_operations = [
        _operation_for(descriptor)
        for descriptor in descriptors
        if descriptor.name not in names
    ]
    return SkillDefinition(
        version=base.version,
        environment=base.environment,
        operations=[*base.operations, *integration_operations],
        default_tier=base.default_tier,
        focus_areas=list(base.focus_areas),
    )


class IntegrationToolRuntime:
    def __init__(
        self,
        factory,
        *,
        org_id: uuid.UUID,
        descriptors: list[IntegrationToolDescriptor],
    ) -> None:
        self._factory = factory
        self._org_id = org_id
        self.descriptors = descriptors
        self._by_name = {item.name: item for item in descriptors}

    @classmethod
    async def create(cls, factory, org_id: uuid.UUID) -> "IntegrationToolRuntime":
        async with factory() as db:
            connectors = await IntegrationConnectorRepo.list_for_org(
                db, org_id, enabled_only=True
            )
        descriptors: list[IntegrationToolDescriptor] = []
        for connector in connectors:
            adapter = get_adapter(connector.kind)
            if adapter is None:
                continue
            for capability in adapter.capabilities:
                descriptors.append(
                    IntegrationToolDescriptor(
                        name=_tool_name(
                            connector.kind, capability.action, connector.id
                        ),
                        description=(
                            f"{capability.description} Connector: "
                            f"{connector.name} ({connector.kind})."
                        ),
                        connector_id=connector.id,
                        capability=capability,
                    )
                )
        return cls(factory, org_id=org_id, descriptors=descriptors)

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._by_name

    @property
    def descriptions(self) -> dict[str, str]:
        return {
            descriptor.name: descriptor.description
            for descriptor in self.descriptors
        }

    async def call_tool(
        self,
        _session,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> CallToolResult:
        descriptor = self._by_name.get(tool_name)
        if descriptor is None:
            return CallToolResult(
                isError=True,
                content=[
                    TextContent(
                        type="text", text=f"Unknown integration tool: {tool_name}"
                    )
                ],
            )
        async with self._factory() as db:
            connector = await IntegrationConnectorRepo.get_by_id(
                db, self._org_id, descriptor.connector_id
            )
            if connector is None or not connector.is_enabled:
                return CallToolResult(
                    isError=True,
                    content=[
                        TextContent(
                            type="text",
                            text="Integration connector is unavailable or disabled",
                        )
                    ],
                )
            adapter = get_adapter(connector.kind)
            if adapter is None:
                return CallToolResult(
                    isError=True,
                    content=[
                        TextContent(
                            type="text",
                            text=f"No adapter is installed for {connector.kind}",
                        )
                    ],
                )
            auth = IntegrationConnectorRepo.decrypt_auth(connector)
            result = await adapter.safe_invoke(
                descriptor.capability.action,
                connector,
                auth,
                parameters or {},
            )
            if descriptor.capability.action == "test_connection":
                await IntegrationConnectorRepo.mark_status(
                    db,
                    connector,
                    status="healthy" if result.ok else "error",
                    error=result.error,
                )
                await db.commit()
        payload = {
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
        }
        return CallToolResult(
            isError=not result.ok,
            content=[
                TextContent(type="text", text=json.dumps(payload, default=str))
            ],
        )
