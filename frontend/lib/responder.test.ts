import { describe, expect, it } from "vitest";

import { acknowledgedByName, responderDisplay } from "@/lib/responder";
import type { IncidentResponse } from "@/lib/types";

function inc(extra: Partial<IncidentResponse>): IncidentResponse {
  return {
    id: "11111111-2222-3333-4444-555555555555",
    title: "T",
    description: "D",
    status: "open",
    severity: "high",
    service_id: null,
    external_id: null,
    external_source: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...extra,
  } as IncidentResponse;
}

describe("responderDisplay", () => {
  it("Unassigned when no responder", () => {
    expect(responderDisplay(inc({ responder_state: "unassigned" }))).toEqual({
      text: "Unassigned",
      tone: "muted",
    });
  });

  it("Awaiting the current escalation target", () => {
    const r = responderDisplay(
      inc({ responder_state: "awaiting", responder_user_id: "u1", responder_display_name: "Alice" }),
    );
    expect(r.text).toBe("Awaiting Alice");
    expect(r.tone).toBe("warn");
  });

  it("Assigned to the acknowledged responder", () => {
    const r = responderDisplay(
      inc({ responder_state: "assigned", responder_user_id: "u1", responder_display_name: "Alice" }),
    );
    expect(r.text).toBe("Assigned to Alice");
    expect(r.tone).toBe("ok");
  });

  it("Escalated to the next target", () => {
    const r = responderDisplay(
      inc({ responder_state: "escalated", responder_user_id: "u2", responder_display_name: "Bob" }),
    );
    expect(r.text).toBe("Escalated to Bob");
  });

  it("falls back to Deleted user <id> when the name is gone", () => {
    const r = responderDisplay(
      inc({
        responder_state: "assigned",
        responder_user_id: "abcdef12-3456-7890-abcd-ef1234567890",
        responder_display_name: null,
      }),
    );
    expect(r.text).toBe("Assigned to Deleted user abcdef12");
  });
});

describe("acknowledgedByName", () => {
  it("returns the name when acknowledged", () => {
    expect(
      acknowledgedByName(inc({ acknowledged_by_user_id: "u1", acknowledged_by_display_name: "Alice" })),
    ).toBe("Alice");
  });
  it("returns null when not acknowledged", () => {
    expect(acknowledgedByName(inc({}))).toBeNull();
  });
});
