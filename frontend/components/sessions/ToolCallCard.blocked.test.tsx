/**
 * M1-89: an action the tier gate blocked is written to the audit log, and the
 * session page's Tool Activity renders that entry as a blocked card with the
 * gate's reason and the parameters the agent proposed.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCallCard } from "@/components/sessions/ToolCallCard";
import { auditEntryToSessionEvent } from "@/lib/sessionAuditEvents";
import type { AuditEntryResponse } from "@/lib/types";

const blockedEntry: AuditEntryResponse = {
  id: "audit-blocked-1",
  session_id: "session-1",
  timestamp: "2026-10-26T12:00:00Z",
  tier: 2,
  entry_type: "tool_call_blocked",
  tool_name: "restart_pod",
  tool_parameters: { pod: "ratings-1" },
  result: null,
  permitted: false,
  block_reason: "Tool 'restart_pod' is disabled at tier 2",
  duration_ms: null,
};

describe("Tool Activity for an action the tier gate blocked", () => {
  it("renders the audit entry as a blocked card with the reason and parameters", () => {
    const event = auditEntryToSessionEvent(blockedEntry, 1);
    render(
      <ToolCallCard
        raw={event.raw as Parameters<typeof ToolCallCard>[0]["raw"]}
        fallbackName={event.label}
        ts={event.ts}
      />,
    );

    const card = screen.getByTestId("tool-call-card");
    expect(card.getAttribute("data-phase")).toBe("blocked");
    expect(card.textContent).toContain("restart_pod");
    expect(screen.getByTestId("blocked-action-callout").textContent).toContain(
      "Tool 'restart_pod' is disabled at tier 2",
    );

    fireEvent.click(screen.getByRole("button", { name: /view parameters/i }));
    expect(card.textContent).toContain("ratings-1");
  });
});
