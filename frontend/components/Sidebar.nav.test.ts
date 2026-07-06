/**
 * Sidebar nav role gating (Part 6) + Agent Teams removal (Part 10).
 * Tests the pure nav model + role filter, not the rendered component.
 */

import { describe, expect, it } from "vitest";

import {
  applyApprovalsBadge,
  buildNavGroups,
  navItemVisibleForRole,
  requiredRolesForPath,
} from "@/components/Sidebar";

function visibleHrefs(role: string): string[] {
  return buildNavGroups()
    .flatMap((g) => g.items)
    .filter((item) => navItemVisibleForRole(item, role))
    .map((item) => item.href);
}

describe("Sidebar nav model", () => {
  it("no longer includes Agent Teams (deferred from v1)", () => {
    const all = buildNavGroups().flatMap((g) => g.items.map((i) => i.href));
    expect(all).not.toContain("/dashboard/agent-teams");
  });

  it("no longer includes multi-org management", () => {
    const all = buildNavGroups().flatMap((g) => g.items.map((i) => i.href));
    expect(all).not.toContain("/dashboard/organizations");
  });

  it("admin sees admin/config surfaces", () => {
    const hrefs = visibleHrefs("admin");
    expect(hrefs).toContain("/dashboard/config");
    expect(hrefs).toContain("/dashboard/people");
    expect(hrefs).toContain("/dashboard/models");
    expect(hrefs).toContain("/dashboard/paging");
    expect(hrefs).not.toContain("/dashboard/paging/services");
    expect(hrefs).toContain("/dashboard/integrations");
  });

  it("labels the skills surface 'MCP Skills'", () => {
    const skillsItem = buildNavGroups()
      .flatMap((g) => g.items)
      .find((i) => i.href === "/dashboard/skills");
    expect(skillsItem?.label).toBe("MCP Skills");
  });

  it("operator does NOT see admin/global config items", () => {
    const hrefs = visibleHrefs("operator");
    for (const adminOnly of [
      "/dashboard/config",
      "/dashboard/people",
      "/dashboard/organizations",
      "/dashboard/skills",
      "/dashboard/models",
      "/dashboard/mcp-servers",
      "/dashboard/memories",
      "/dashboard/integrations",
    ]) {
      expect(hrefs).not.toContain(adminOnly);
    }
    // Operators CAN see the Paging hub in read-only mode.
    expect(hrefs).toContain("/dashboard/paging");
    expect(hrefs).not.toContain("/dashboard/paging/services");
    expect(hrefs).not.toContain("/dashboard/paging/teams");
    // Keeps incident-response surfaces.
    expect(hrefs).toContain("/dashboard/incidents");
    expect(hrefs).toContain("/dashboard/approvals");
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
    expect(hrefs).not.toContain("/dashboard/paging");
  });

  it("adds and caps the pending approvals badge", () => {
    const withBadge = applyApprovalsBadge(buildNavGroups(), 12)
      .flatMap((g) => g.items)
      .find((i) => i.href === "/dashboard/approvals");
    expect(withBadge?.badge).toEqual({ label: "9+", tone: "warn" });

    const withoutBadge = applyApprovalsBadge(buildNavGroups(), 0)
      .flatMap((g) => g.items)
      .find((i) => i.href === "/dashboard/approvals");
    expect(withoutBadge?.badge).toBeUndefined();
  });
});

describe("requiredRolesForPath (route guard)", () => {
  it("restricts admin routes (incl. nested detail paths)", () => {
    expect(requiredRolesForPath("/dashboard/config")).toEqual(["admin"]);
    expect(requiredRolesForPath("/dashboard/people")).toEqual(["admin"]);
    expect(requiredRolesForPath("/dashboard/people/detail")).toEqual(["admin"]);
    expect(requiredRolesForPath("/dashboard/models")).toEqual(["admin"]);
    expect(requiredRolesForPath("/dashboard/integrations")).toEqual(["admin"]);
  });

  it("paging setup is read-only for operators (admin+operator)", () => {
    // Operators can view Teams/Chains/Services/Rosters/Maintenance Windows read-only.
    for (const route of [
      "/dashboard/paging/services",
      "/dashboard/paging/teams",
      "/dashboard/paging/rosters",
      "/dashboard/paging/escalation-chains",
      "/dashboard/paging/maintenance-windows",
    ]) {
      expect(requiredRolesForPath(route)).toEqual(["admin", "operator"]);
    }
  });

  it("allows shared routes (null = everyone) and self-service settings", () => {
    expect(requiredRolesForPath("/dashboard")).toBeNull();
    expect(requiredRolesForPath("/dashboard/incidents")).toBeNull();
    expect(requiredRolesForPath("/dashboard/incidents/detail")).toBeNull();
    expect(requiredRolesForPath("/dashboard/settings/profile")).toBeNull();
  });

  it("scopes operator-and-admin routes", () => {
    expect(requiredRolesForPath("/dashboard/approvals")).toEqual(["admin", "operator"]);
    expect(requiredRolesForPath("/dashboard/reliability")).toEqual(["admin", "operator"]);
  });
});
