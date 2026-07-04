import { describe, expect, it } from "vitest";

import { deriveStates, workflowHeaderLabel } from "./SessionWorkflowState";

const nodeEvent = (label: string) => ({ kind: "node", label });

describe("deriveStates", () => {
  it("marks the most recent node current for an active session", () => {
    const states = deriveStates({
      sessionStatus: "active",
      events: [nodeEvent("Observe"), nodeEvent("Diagnose")],
    });
    expect(states.find((s) => s.key === "observe")?.status).toBe("done");
    expect(states.find((s) => s.key === "diagnose")?.status).toBe("current");
    expect(states.find((s) => s.key === "execute")?.status).toBe("pending");
  });

  it("marks stages done and Done current for a completed session", () => {
    const states = deriveStates({ sessionStatus: "completed", events: [] });
    expect(states.find((s) => s.key === "completed")?.status).toBe("current");
    expect(states.find((s) => s.key === "observe")?.status).toBe("done");
    expect(states.find((s) => s.key === "execute")?.status).toBe("done");
    // awaiting_approval is a sub-state; when never reached it reads "skipped".
    expect(states.find((s) => s.key === "awaiting_approval")?.status).toBe("skipped");
    expect(states.some((s) => s.status === "pending")).toBe(false);
  });

  it("freezes a cancelled session — no current/pulsing step", () => {
    const states = deriveStates({ sessionStatus: "cancelled", events: [] });
    expect(states.some((s) => s.status === "current")).toBe(false);
    expect(states.every((s) => s.status === "pending")).toBe(true);
  });

  it("freezes reached stages as done for a failed session, none current", () => {
    const states = deriveStates({
      sessionStatus: "failed",
      events: [nodeEvent("Observe"), nodeEvent("Diagnose"), nodeEvent("Execute")],
    });
    expect(states.some((s) => s.status === "current")).toBe(false);
    expect(states.find((s) => s.key === "observe")?.status).toBe("done");
    expect(states.find((s) => s.key === "execute")?.status).toBe("done");
    expect(states.find((s) => s.key === "verify")?.status).toBe("pending");
  });
});

describe("workflowHeaderLabel", () => {
  it("uses the current stage label when live", () => {
    expect(workflowHeaderLabel("active", "Executing")).toBe("Executing");
  });

  it("shows Initializing when active with no stage yet", () => {
    expect(workflowHeaderLabel("active", null)).toBe("Initializing…");
  });

  it("shows a terminal label with the summary for a cancelled session", () => {
    expect(workflowHeaderLabel("cancelled", null, "AI session queue wait expired.")).toBe(
      "Cancelled — AI session queue wait expired.",
    );
    expect(workflowHeaderLabel("cancelled", null)).toBe("Cancelled");
    expect(workflowHeaderLabel("timed_out", null, "  ")).toBe("Timed out");
  });
});
