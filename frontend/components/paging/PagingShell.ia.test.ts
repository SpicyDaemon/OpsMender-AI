import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { buildNavGroups } from "@/components/Sidebar";
import { TABS } from "@/components/paging/PagingShell";

const pagingShellSource = readFileSync(
  join(process.cwd(), "components", "paging", "PagingShell.tsx"),
  "utf8",
);

describe("v1 paging IA", () => {
  it("shows the simplified Paging & On-call sidebar entries", () => {
    const paging = buildNavGroups(false).find((group) => group.id === "paging");
    expect(paging?.items.map((item) => item.label)).toEqual([
      "Teams",
      "Escalation Chains",
      "Services",
      "Rosters",
      "Maintenance Windows",
      "Notifications",
    ]);
  });

  it("orders the Paging tabs around the v1 workflow", () => {
    expect(TABS.map((tab) => tab.label)).toEqual([
      "Teams",
      "Escalation Chains",
      "Services",
      "Rosters",
      "Maintenance Windows",
      "Notifications",
    ]);
  });

  it("keeps the Services form focused on v1 alert intake", () => {
    expect(pagingShellSource).toContain("Priority");
    expect(pagingShellSource).toContain("Preferred MCP servers");
    expect(pagingShellSource).toContain("Preferred Models");
    expect(pagingShellSource).toContain("maxSelections={3}");
    expect(pagingShellSource).toContain(
      "The model that ingests an incident becomes the default model",
    );
    expect(pagingShellSource).not.toContain("Incident auto-start policy");
    expect(pagingShellSource).toContain("service webhook");
    expect(pagingShellSource).not.toContain(["Source", "account"].join(" "));
    expect(pagingShellSource).not.toContain(["Allowed", "MCP", "servers"].join(" "));
  });

  it("uses schedule language for rosters and drop language for maintenance", () => {
    expect(pagingShellSource).toContain("Start Date");
    expect(pagingShellSource).toContain("Coverage window");
    expect(pagingShellSource).toContain("Matching alerts are dropped");
    // Ordered rotation members use a checkbox/chip multi-select, not native
    // Ctrl/Cmd <select multiple>.
    expect(pagingShellSource).toContain("Rotation members (ordered)");
  });

  it("organizes Notifications into the four v1 tabs", () => {
    expect(pagingShellSource).toContain("My Routing");
    expect(pagingShellSource).toContain("Routing Summary");
    expect(pagingShellSource).toContain("Notification Channels");
    // Outbound Hooks is renamed to Viewer Notifications.
    expect(pagingShellSource).toContain("Viewer Notifications");
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

});
