import { describe, expect, it } from "vitest";
import {
  alertGroupingLabel,
  auditEntryTypeLabel,
  flappingIncidentBadgeLabel,
  groupedAlertBadgeLabel,
  providerName,
  sessionPrimaryLabel,
  titleCaseIdentifier,
  workflowNodeLabel,
} from "./displayNames";

describe("display name helpers", () => {
  it("maps audit entry and workflow identifiers to operator-facing labels", () => {
    expect(auditEntryTypeLabel("session_start")).toBe("Session started");
    expect(auditEntryTypeLabel("post")).toBe("Tool result");
    expect(workflowNodeLabel("tier_gate")).toBe("Tier gate");
    expect(workflowNodeLabel("custom_step")).toBe("Custom Step");
  });

  it("preserves provider brand casing", () => {
    expect(providerName("openai")).toBe("OpenAI");
    expect(providerName("openai_compatible")).toBe("OpenAI-compatible");
    expect(providerName("bedrock")).toBe("AWS Bedrock");
    expect(providerName("custom_provider")).toBe("Custom Provider");
  });

  it("formats generic identifiers consistently", () => {
    expect(titleCaseIdentifier("awaiting_approval")).toBe("Awaiting Approval");
    expect(titleCaseIdentifier("timed-out")).toBe("Timed Out");
    expect(titleCaseIdentifier(null)).toBe("Unknown");
  });

  it("labels alert-noise controls and badges", () => {
    expect(alertGroupingLabel("inherit")).toBe("Inherit (workspace default)");
    expect(alertGroupingLabel("on")).toBe("On");
    expect(alertGroupingLabel("off")).toBe("Off");
    expect(groupedAlertBadgeLabel(3)).toBe("×3 grouped");
    expect(flappingIncidentBadgeLabel()).toBe("Flapping");
  });

  it("never uses a session id as the primary session label", () => {
    const session = { id: "12345678-aaaa-bbbb-cccc-123456789abc" };
    expect(sessionPrimaryLabel(session, new Map())).toBe("Untitled session");

    const incidentById = new Map([
      ["inc-1", { id: "inc-1", title: "[queue-demo] P0 sample #1" }],
    ]);
    expect(
      sessionPrimaryLabel(
        { ...session, incident_id: "inc-1", summary: "fallback summary" },
        incidentById,
      ),
    ).toBe("[queue-demo] P0 sample #1");
    expect(
      sessionPrimaryLabel({ ...session, summary: "  fallback summary  " }, new Map()),
    ).toBe("fallback summary");
  });
});
