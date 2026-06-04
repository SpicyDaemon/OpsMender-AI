/**
 * Sidebar nav role gating (Part 6) + Agent Teams removal (Part 10).
 * Tests the pure nav model + role filter, not the rendered component.
 */

import { describe, expect, it } from "vitest";

import { buildNavGroups, navItemVisibleForRole } from "@/components/Sidebar";

function visibleHrefs(role: string): string[] {
  return buildNavGroups(false)
    .flatMap((g) => g.items)
    .filter((item) => navItemVisibleForRole(item, role))
    .map((item) => item.href);
}

describe("Sidebar nav model", () => {
  it("no longer includes Agent Teams (deferred from v1)", () => {
    const all = buildNavGroups(false).flatMap((g) => g.items.map((i) => i.href));
    expect(all).not.toContain("/dashboard/agent-teams");
  });

  it("admin sees admin/config surfaces", () => {
    const hrefs = visibleHrefs("admin");
    expect(hrefs).toContain("/dashboard/config");
    expect(hrefs).toContain("/dashboard/people");
    expect(hrefs).toContain("/dashboard/models");
    expect(hrefs).toContain("/dashboard/paging/services");
  });

  it("operator does NOT see admin/global config items", () => {
    const hrefs = visibleHrefs("operator");
    for (const admin of [
      "/dashboard/config",
      "/dashboard/people",
      "/dashboard/organizations",
      "/dashboard/skills",
      "/dashboard/models",
      "/dashboard/mcp-servers",
      "/dashboard/memories",
      "/dashboard/paging/services",
      "/dashboard/paging/teams",
    ]) {
      expect(hrefs).not.toContain(admin);
    }
    // But keeps its incident-response surfaces.
    expect(hrefs).toContain("/dashboard/incidents");
    expect(hrefs).toContain("/dashboard/approvals");
    expect(hrefs).toContain("/dashboard/paging/notifications");
  });

  it("viewer is limited to read-only surfaces", () => {
    const hrefs = visibleHrefs("viewer");
    expect(hrefs).toEqual(
      expect.arrayContaining(["/dashboard", "/dashboard/incidents"]),
    );
    // No approvals (acknowledge/resolve), no admin/config, no paging mgmt.
    expect(hrefs).not.toContain("/dashboard/approvals");
    expect(hrefs).not.toContain("/dashboard/config");
    expect(hrefs).not.toContain("/dashboard/people");
    expect(hrefs).not.toContain("/dashboard/paging/services");
  });
});
