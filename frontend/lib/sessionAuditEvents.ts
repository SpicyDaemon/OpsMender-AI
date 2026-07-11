import type { AuditEntryResponse } from "@/lib/types";
import { titleCaseIdentifier, workflowNodeLabel } from "@/lib/displayNames";

export type SessionEventKind =
  | "node"
  | "tool"
  | "approval"
  | "error"
  | "end"
  | "llm"
  | "tier_gate";

export interface SessionLogEvent {
  id: number;
  kind: SessionEventKind;
  label: string;
  detail?: string;
  ts: Date;
  durationMs?: number;
  raw?: Record<string, unknown>;
}

function detailFromResult(result: Record<string, unknown> | null): string | undefined {
  if (!result) return undefined;
  const message = result.message ?? result.summary ?? result.status;
  return typeof message === "string" ? message : JSON.stringify(result, null, 2);
}

export function auditEntryToSessionEvent(
  entry: AuditEntryResponse,
  id: number,
): SessionLogEvent {
  const ts = new Date(entry.timestamp);
  const entryType = entry.entry_type.toLowerCase();

  if (entryType.startsWith("tool_call")) {
    const phase =
      entryType === "tool_call_start"
        ? "start"
        : entryType === "tool_call_blocked" || !entry.permitted
          ? "blocked"
          : "end";
    const classification = entry.result?.classification;
    return {
      id,
      kind: "tool",
      label: entry.tool_name ?? "unknown",
      detail:
        phase === "blocked"
          ? `BLOCKED — ${entry.block_reason ?? "Policy denied this operation"}`
          : JSON.stringify(entry.tool_parameters ?? {}, null, 2),
      ts,
      durationMs: entry.duration_ms ?? undefined,
      raw: {
        audit_id: entry.id,
        tool_name: entry.tool_name ?? "unknown",
        parameters: entry.tool_parameters ?? {},
        result: entry.result,
        permitted: entry.permitted,
        phase,
        block_reason: entry.block_reason,
        duration_ms: entry.duration_ms ?? undefined,
        classification:
          typeof classification === "string" ? classification : undefined,
      },
    };
  }

  if (entryType === "node_transition") {
    const node = String(entry.result?.node ?? entry.tool_name ?? "unknown");
    return {
      id,
      kind: node === "tier_gate" ? "tier_gate" : "node",
      label: workflowNodeLabel(node),
      detail: detailFromResult(entry.result),
      ts,
      durationMs: entry.duration_ms ?? undefined,
      raw: { audit_id: entry.id, ...(entry.result ?? {}) },
    };
  }

  if (entryType.startsWith("approval_")) {
    return {
      id,
      kind: "approval",
      label: titleCaseIdentifier(entryType),
      detail: detailFromResult(entry.result),
      ts,
      raw: { audit_id: entry.id, ...(entry.result ?? {}) },
    };
  }

  if (entryType === "session_end") {
    return {
      id,
      kind: "end",
      label: "Session ended",
      detail: detailFromResult(entry.result),
      ts,
      raw: { audit_id: entry.id, ...(entry.result ?? {}) },
    };
  }

  if (entryType === "error") {
    return {
      id,
      kind: "error",
      label: "Error",
      detail: detailFromResult(entry.result),
      ts,
      raw: { audit_id: entry.id, ...(entry.result ?? {}) },
    };
  }

  return {
    id,
    kind: "node",
    label: titleCaseIdentifier(entryType),
    detail: detailFromResult(entry.result),
    ts,
    raw: { audit_id: entry.id, ...(entry.result ?? {}) },
  };
}
