import { describe, expect, it } from "vitest";
import type { AuditEntryResponse } from "@/lib/types";
import { auditEntryToSessionEvent } from "@/lib/sessionAuditEvents";

function entry(overrides: Partial<AuditEntryResponse>): AuditEntryResponse {
  return {
    id: "audit-1",
    session_id: "session-1",
    timestamp: "2026-07-10T12:00:00Z",
    tier: 0,
    entry_type: "tool_call",
    tool_name: "kubectl_rollout_restart",
    tool_parameters: { deployment: "checkout-api" },
    result: { ok: true, classification: "caution" },
    permitted: true,
    block_reason: null,
    duration_ms: 824,
    ...overrides,
  };
}

describe("persisted session audit events", () => {
  it("hydrates a completed governed tool call", () => {
    const event = auditEntryToSessionEvent(entry({}), 1);
    expect(event.kind).toBe("tool");
    expect(event.raw).toMatchObject({
      phase: "end",
      permitted: true,
      classification: "caution",
    });
    expect(event.durationMs).toBe(824);
  });

  it("hydrates a blocked tool call with its reason", () => {
    const event = auditEntryToSessionEvent(
      entry({
        entry_type: "tool_call_blocked",
        permitted: false,
        block_reason: "Advisory tier does not execute writes",
      }),
      2,
    );
    expect(event.raw).toMatchObject({ phase: "blocked", permitted: false });
    expect(event.detail).toContain("Advisory tier");
  });

  it("hydrates workflow and approval transitions", () => {
    const workflow = auditEntryToSessionEvent(
      entry({
        entry_type: "node_transition",
        tool_name: null,
        result: { node: "tier_gate", status: "completed" },
      }),
      3,
    );
    const approval = auditEntryToSessionEvent(
      entry({
        entry_type: "approval_requested",
        tool_name: null,
        result: { message: "Waiting for operator approval" },
      }),
      4,
    );
    expect(workflow.kind).toBe("tier_gate");
    expect(approval.kind).toBe("approval");
    expect(approval.detail).toBe("Waiting for operator approval");
  });
});
