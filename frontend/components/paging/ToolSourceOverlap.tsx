import { AlertTriangle } from "lucide-react";

import type { ServiceResponse, ToolSourceOverlap } from "@/lib/types";

/**
 * Advisory UI for a service whose MCP server and native connector appear to
 * reach the same system. It never blocks anything: both sources still reach
 * the AI, which picks between them by description.
 */

const OVERLAP_HINT =
  "The AI sees both and picks by description, and only the native connector " +
  "links tickets to the incident. Keep one source per capability.";

/** Secret-free, one-line summary of overlapping tool sources on a service. */
export function toolSourceOverlapSummary(overlaps: ToolSourceOverlap[]): string {
  return overlaps
    .map(
      (o) =>
        `${o.connector_name} (${o.kind} connector) and MCP server ${o.mcp_server_name}`,
    )
    .join("; ");
}

/** The Services table "Toolset" cell, with the overlap warning when present. */
export function ServiceToolsetCell({ service }: { service: ServiceResponse }) {
  const overlaps = service.tool_source_overlaps ?? [];
  const label =
    service.mcp_server_ids.length > 0 ? (
      <span className="text-xs text-fg-secondary">MCP covered</span>
    ) : service.allowed_integration_connector_ids.length > 0 ? (
      <span className="text-xs text-fg-secondary">
        Integrations are covering this service&apos;s toolset
      </span>
    ) : (
      <span className="text-xs text-fg-muted">Advisory only</span>
    );
  if (overlaps.length === 0) return label;
  return (
    <div className="flex flex-col gap-0.5">
      {label}
      <span
        className="flex items-start gap-1 text-[11px] font-medium text-status-high"
        title={`${toolSourceOverlapSummary(overlaps)}. ${OVERLAP_HINT}`}
      >
        <AlertTriangle size={12} className="mt-px shrink-0" aria-hidden="true" />
        <span>Overlapping tool sources</span>
      </span>
    </div>
  );
}

/**
 * Editor note for the saved overlaps whose two sources are still selected in
 * the form. Deselecting either source hides it immediately; overlaps created
 * by unsaved selections appear after saving.
 */
export function ToolSourceOverlapNote({
  overlaps,
  selectedMcpServerIds,
  selectedConnectorIds,
}: {
  overlaps: ToolSourceOverlap[] | undefined;
  selectedMcpServerIds: string[];
  selectedConnectorIds: string[];
}) {
  const current = (overlaps ?? []).filter(
    (o) =>
      selectedMcpServerIds.includes(o.mcp_server_id) &&
      selectedConnectorIds.includes(o.connector_id),
  );
  if (current.length === 0) return null;
  return (
    <div
      role="note"
      className="mt-2 flex items-start gap-1.5 rounded-md border border-status-high-border bg-status-high-bg px-2.5 py-2 text-xs text-status-high"
    >
      <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>
        Overlapping tool sources: {toolSourceOverlapSummary(current)}. {OVERLAP_HINT}{" "}
        To keep the MCP server, deny the connector&apos;s tools in its skill;
        otherwise remove one of them here.
      </span>
    </div>
  );
}
