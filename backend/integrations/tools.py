"""Internal integration tools bridged into the existing tier/audit pipeline."""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any

from mcp.types import CallToolResult, TextContent

from backend.db.repos import (
    IncidentIntegrationLinkRepo,
    IntegrationConnectorRepo,
    SkillRepo,
    TicketSyncStateRepo,
)
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
    authored_operation: OperationClassification | None = None


def _tool_name(kind: str, action: str, connector_id: uuid.UUID) -> str:
    return f"integration__{kind}__{action}__{connector_id.hex}"


def _operation_for(
    descriptor: IntegrationToolDescriptor,
) -> OperationClassification:
    capability = descriptor.capability
    tiers = {
        0: OperationTierPolicy(
            enabled=not capability.mutating and not capability.always_requires_approval,
            mode=(
                "autonomous"
                if not capability.mutating and not capability.always_requires_approval
                else "blocked"
            ),
            require_reversible=False,
        ),
        1: OperationTierPolicy(
            enabled=True,
            mode=(
                "approval"
                if capability.mutating or capability.always_requires_approval
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


_CLASSIFICATION_RANK = {"safe": 0, "caution": 1, "destructive": 2}
_MODE_RANK = {"blocked": 0, "advisory": 0, "approval": 1, "autonomous": 2}


def _restrict_operation(
    capability_policy: OperationClassification,
    authored_policy: OperationClassification | None,
) -> OperationClassification:
    """Intersect authored policy with the connector capability baseline."""
    if authored_policy is None:
        return capability_policy
    classification = max(
        (capability_policy.classification, authored_policy.classification),
        key=lambda value: _CLASSIFICATION_RANK.get(value, 2),
    )
    if capability_policy.deny or authored_policy.deny:
        return OperationClassification(
            tool=capability_policy.tool,
            classification=classification,
            notes=authored_policy.notes or capability_policy.notes,
            deny=True,
        )

    tiers: dict[int, OperationTierPolicy] = {}
    for tier in (0, 1, 2):
        baseline = capability_policy.policy_for_tier(tier)
        authored = authored_policy.policy_for_tier(tier)
        if baseline is None or authored is None:
            tiers[tier] = OperationTierPolicy(enabled=False, mode="blocked")
            continue
        mode = min(
            (baseline.mode, authored.mode),
            key=lambda value: _MODE_RANK.get(value, 0),
        )
        if _MODE_RANK.get(mode, 0) == 0:
            mode = (
                "blocked" if "blocked" in {baseline.mode, authored.mode} else "advisory"
            )
        require_reversible = (
            True
            if True in {baseline.require_reversible, authored.require_reversible}
            else (
                False
                if baseline.require_reversible is False
                and authored.require_reversible is False
                else None
            )
        )
        tiers[tier] = OperationTierPolicy(
            enabled=baseline.enabled and authored.enabled,
            mode=mode,
            require_reversible=require_reversible,
        )
    return OperationClassification(
        tool=capability_policy.tool,
        classification=classification,
        notes=authored_policy.notes or capability_policy.notes,
        reversible=(
            capability_policy.effective_reversible
            and authored_policy.effective_reversible
        ),
        compensating_inverse=authored_policy.compensating_inverse,
        allow_generic=authored_policy.allow_generic,
        tiers=tiers,
    )


def merge_integration_skill(
    base: SkillDefinition,
    descriptors: list[IntegrationToolDescriptor],
) -> SkillDefinition:
    descriptor_names = {descriptor.name for descriptor in descriptors}
    base_operations = [
        operation
        for operation in base.operations
        if operation.tool not in descriptor_names
    ]
    integration_operations = []
    for descriptor in descriptors:
        operation = _operation_for(descriptor)
        operation = _restrict_operation(operation, base.operation_for(descriptor.name))
        operation = _restrict_operation(operation, descriptor.authored_operation)
        integration_operations.append(operation)
    return SkillDefinition(
        version=base.version,
        environment=base.environment,
        operations=[*base_operations, *integration_operations],
        default_tier=base.default_tier,
        focus_areas=list(base.focus_areas),
        workflow=list(base.workflow),
        custom_instructions=dict(base.custom_instructions),
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
    async def create(
        cls,
        factory,
        org_id: uuid.UUID,
        *,
        allowed_connector_ids: set[uuid.UUID] | None = None,
    ) -> "IntegrationToolRuntime":
        """Build the integration tool surface for one org.

        ``allowed_connector_ids`` is the strict per-service integration
        allowlist. When it is ``None`` (no service context — e.g. a session not
        bound to an incident/service) every enabled connector is exposed, which
        preserves prior behavior. When it is a set (including an **empty** set)
        only connectors in that set are exposed — an empty allowlist therefore
        yields no integration tools at all, which is the strict-allowlist
        semantics services opt into."""

        async with factory() as db:
            connectors = await IntegrationConnectorRepo.list_for_org(
                db, org_id, enabled_only=True
            )
            authored_skills = {
                connector.id: await SkillRepo.get_for_integration_connector(
                    db, org_id, connector.id
                )
                for connector in connectors
            }
        if allowed_connector_ids is not None:
            connectors = [
                connector
                for connector in connectors
                if connector.id in allowed_connector_ids
            ]
        descriptors: list[IntegrationToolDescriptor] = []
        for connector in connectors:
            adapter = get_adapter(connector.kind)
            if adapter is None:
                continue
            authored_definition = None
            authored_failed = False
            authored_skill = authored_skills.get(connector.id)
            if authored_skill is not None:
                try:
                    from backend.skills.parser import loads

                    authored_definition = loads(authored_skill.content_md)
                except Exception:  # noqa: BLE001 - malformed policy fails closed
                    authored_failed = True
            for capability in adapter.capabilities:
                name = _tool_name(connector.kind, capability.action, connector.id)
                authored_operation = (
                    OperationClassification(
                        tool=name,
                        classification="destructive",
                        deny=True,
                        notes="Malformed connector skill; denied fail-closed.",
                    )
                    if authored_failed
                    else (
                        None
                        if authored_definition is None
                        else authored_definition.operation_for(name)
                    )
                )
                descriptors.append(
                    IntegrationToolDescriptor(
                        name=name,
                        description=(
                            f"{capability.description} Connector: "
                            f"{connector.name} ({connector.kind})."
                        ),
                        connector_id=connector.id,
                        capability=capability,
                        authored_operation=authored_operation,
                    )
                )
        return cls(factory, org_id=org_id, descriptors=descriptors)

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._by_name

    @property
    def descriptions(self) -> dict[str, str]:
        return {
            descriptor.name: descriptor.description for descriptor in self.descriptors
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
            link_payload = result.data.get("integration_link")
            if result.ok and isinstance(link_payload, dict):
                try:
                    incident_id = uuid.UUID(str(link_payload["incident_id"]))
                    link = await IncidentIntegrationLinkRepo.upsert(
                        db,
                        self._org_id,
                        incident_id=incident_id,
                        connector_id=connector.id,
                        reference_type=str(link_payload["reference_type"]),
                        external_id=str(link_payload["external_id"]),
                        url=str(link_payload["url"]),
                        title=(
                            str(link_payload["title"])
                            if link_payload.get("title")
                            else None
                        ),
                        reference_meta=link_payload.get("reference_meta") or {},
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    result = dataclasses.replace(
                        result,
                        ok=False,
                        error=f"Invalid integration link payload: {exc}",
                    )
                else:
                    if link is None:
                        result = dataclasses.replace(
                            result,
                            ok=False,
                            error=(
                                "Incident or integration connector was not "
                                "found in this organization"
                            ),
                        )
            sync_payload = result.data.get("ticket_sync")
            if (
                result.ok
                and isinstance(sync_payload, dict)
                and bool(connector.config.get("ticket_sync_enabled"))
                and connector.kind in {"jira", "servicenow"}
            ):
                try:
                    await TicketSyncStateRepo.upsert(
                        db,
                        self._org_id,
                        connector_id=connector.id,
                        incident_id=uuid.UUID(str(sync_payload["incident_id"])),
                        external_ticket_id=str(sync_payload["external_ticket_id"]),
                        external_ticket_url=(
                            str(sync_payload["external_ticket_url"])
                            if sync_payload.get("external_ticket_url")
                            else None
                        ),
                        status_map=dict(connector.config.get("status_map") or {}),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    result = dataclasses.replace(
                        result,
                        ok=False,
                        error=f"Invalid ticket sync payload: {exc}",
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
            content=[TextContent(type="text", text=json.dumps(payload, default=str))],
        )
