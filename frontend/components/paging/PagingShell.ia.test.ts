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
    expect(pagingShellSource).toContain("service webhook");
    expect(pagingShellSource).not.toContain(["Source", "account"].join(" "));
    expect(pagingShellSource).not.toContain(["Allowed", "MCP", "servers"].join(" "));
  });

  it("uses schedule language for rosters and drop language for maintenance", () => {
    expect(pagingShellSource).toContain("Start Date");
    expect(pagingShellSource).toContain("Coverage window");
    expect(pagingShellSource).toContain("Matching alerts are dropped");
  });

  it("consolidates notification concepts into one Notifications page", () => {
    expect(pagingShellSource).toContain("Operator Delivery");
    expect(pagingShellSource).toContain("Viewer Updates");
    expect(pagingShellSource).toContain("Quiet Hours");
    expect(pagingShellSource).toContain("Routing by Priority");
    expect(pagingShellSource).toContain("Sessions / Chat");
  });
});
