import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { buildNavGroups } from "@/components/Sidebar";
import {
  firstCoveredCalendarUserId,
  TABS,
} from "@/components/paging/PagingShell";
import type {
  EscalationCalendarLevel,
  EscalationCalendarResponse,
} from "@/lib/types";

const pagingShellSource = readFileSync(
  join(process.cwd(), "components", "paging", "PagingShell.tsx"),
  "utf8",
);

function calendarLevel(
  status: EscalationCalendarLevel["status"],
  resolvedUserId: string | null,
): EscalationCalendarLevel {
  return {
    level: 1,
    target_type: "user",
    target_id: "target-1",
    target_name: "Primary",
    resolved_user_id: resolvedUserId,
    resolved_user_name: null,
    resolved_user_email: null,
    coverage_start: null,
    coverage_end: null,
    coverage_time_zone: null,
    status,
    warnings: [],
  };
}

describe("v1 paging IA", () => {
  it("shows the simplified Paging & On-call sidebar entries", () => {
    const paging = buildNavGroups().find((group) => group.id === "paging");
    // On Call Schedule (viewer-visible, read-only) leads the group; the hub
    // owns the detailed Teams/Rosters/Chains/Services/Windows/Notifications tabs.
    expect(paging?.items.map((item) => item.label)).toEqual([
      "On Call Schedule",
      "Paging",
    ]);
  });

  it("orders the Paging tabs around the v1 workflow", () => {
    expect(TABS.map((tab) => tab.label)).toEqual([
      "Teams",
      "Rosters",
      "Escalation Chains",
      "Services",
      "Maintenance Windows",
      "Notifications",
    ]);
  });

  it("keeps the Services form focused on v1 alert intake", () => {
    expect(pagingShellSource).toContain("Priority");
    expect(pagingShellSource).toContain("MCP servers");
    expect(pagingShellSource).toContain("Models");
    expect(pagingShellSource).toContain("maxSelections={3}");
    expect(pagingShellSource).toContain(
      "Sessions for this service can use these models.",
    );
    expect(pagingShellSource).toContain("Strict allowlist");
    expect(pagingShellSource).toContain("native integrations cover the");
    expect(pagingShellSource).toContain("advisory-only");
    expect(pagingShellSource).toContain("Allowed integrations");
    expect(pagingShellSource).not.toContain("Incident auto-start policy");
    expect(pagingShellSource).toContain("service webhook");
    expect(pagingShellSource).not.toContain(["Source", "account"].join(" "));
  });

  it("uses schedule language for rosters and drop language for maintenance", () => {
    expect(pagingShellSource).toContain("Start Date");
    expect(pagingShellSource).toContain("Coverage window");
    expect(pagingShellSource).toContain("Matching alerts are dropped");
    // Ordered rotation members use a checkbox/chip multi-select, not native
    // Ctrl/Cmd <select multiple>.
    expect(pagingShellSource).toContain("Rotation members (ordered)");
  });

  it("organizes Notifications around Respond and Track configuration", () => {
    expect(pagingShellSource).toContain("My Routing");
    expect(pagingShellSource).toContain("Routing Summary");
    expect(pagingShellSource).toContain("Notification Channels");
    expect(pagingShellSource).not.toContain("Viewer Notifications");
    expect(pagingShellSource).not.toContain("viewer updates");
    expect(pagingShellSource).not.toContain("Outbound Hooks");
    // Routing Summary is read-only and explains the derivation + defers
    // editable team routing to v1.1.
    expect(pagingShellSource).toContain(
      "Editable team-level routing defaults are planned",
    );
  });

  it("uses a priority-based routing editor with quiet hours", () => {
    expect(pagingShellSource).toContain("Routing by Priority");
    expect(pagingShellSource).toContain("Test notification");
    // P0 always pages through quiet hours (single-line, line-wrap safe).
    expect(pagingShellSource).toContain(
      "Quiet hours apply to P1, P2, and P3 only.",
    );
    // Ordered escalation stages (not a checkbox matrix), max 3 per priority.
    expect(pagingShellSource).toContain("Add stage");
    expect(pagingShellSource).toContain("stages.length >= 3");
    expect(pagingShellSource).toContain("Stage {idx + 1}");
    expect(pagingShellSource).toContain("SMS (Unavailable)");
    expect(pagingShellSource).toContain("Voice Call (Unavailable)");
    expect(pagingShellSource).toContain("Settings → Voice &amp; SMS calling");
  });

  it("uses checkbox/chip multi-selects instead of native multi-select", () => {
    // No remaining native <select multiple> in the paging surface.
    expect(pagingShellSource).not.toContain("multiple");
    expect(pagingShellSource).toContain("<MultiSelect");
  });

  it("gives every paging table an edit action and a copyable intake URL", () => {
    expect(pagingShellSource).toContain("Edit team");
    expect(pagingShellSource).toContain("Edit chain");
    expect(pagingShellSource).toContain("Edit roster");
    expect(pagingShellSource).toContain("Edit window");
    expect(pagingShellSource).toContain("Edit service");
    expect(pagingShellSource).toContain("Alert intake URL");
    expect(pagingShellSource).toContain("<CopyButton");
  });

  it("labels escalation steps as Levels", () => {
    expect(pagingShellSource).toContain("Level {idx + 1}");
  });

  it("adds a read-only escalation chain calendar", () => {
    expect(pagingShellSource).toContain("View escalation calendar");
    expect(pagingShellSource).toContain("Escalation Calendar");
    expect(pagingShellSource).toContain("This shows who will be contacted at each escalation level");
    expect(pagingShellSource).toContain("Coverage is resolved from escalation levels");
    expect(pagingShellSource).toContain("CALENDAR_RANGES");
    expect(pagingShellSource).toContain("empty_roster");
    expect(pagingShellSource).toContain("disabled_roster");
  });

  it("resolves the service on-call user from covered escalation calendar levels", () => {
    const calendar = {
      days: [
        {
          date: "2026-07-04",
          levels: [
            calendarLevel("outside_coverage", null),
            calendarLevel("covered", "user-primary"),
            calendarLevel("covered", "user-secondary"),
          ],
        },
      ],
    } satisfies Pick<EscalationCalendarResponse, "days">;

    expect(firstCoveredCalendarUserId(calendar)).toBe("user-primary");
    expect(
      firstCoveredCalendarUserId({
        days: [{ date: "2026-07-04", levels: [calendarLevel("empty_roster", null)] }],
      }),
    ).toBeNull();
  });
});
