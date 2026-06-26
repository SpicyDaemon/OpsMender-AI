import { describe, expect, it } from "vitest";
import {
  isDashboardHref,
  isOrgScopedDashboardHref,
  scopeDashboardHref,
  scopeDashboardPath,
  stripOrgScope,
} from "./org-path";

describe("organization-scoped dashboard paths", () => {
  it("adds the organization slug to dashboard paths", () => {
    expect(scopeDashboardPath("/dashboard/incidents", "acme")).toBe(
      "/org/acme/dashboard/incidents",
    );
  });

  it("adds the organization slug to dashboard hrefs while preserving query and hash", () => {
    expect(scopeDashboardHref("/dashboard/incidents?new=1#form", "acme")).toBe(
      "/org/acme/dashboard/incidents?new=1#form",
    );
  });

  it("does not double-scope organization paths", () => {
    expect(scopeDashboardHref("/org/acme/dashboard/incidents", "main")).toBe(
      "/org/acme/dashboard/incidents",
    );
  });

  it("recognizes dashboard hrefs that need dashboard-aware handling", () => {
    expect(isDashboardHref("/dashboard/incidents")).toBe(true);
    expect(isDashboardHref("/org/acme/dashboard/incidents")).toBe(true);
    expect(isOrgScopedDashboardHref("/org/acme/dashboard/incidents")).toBe(true);
    expect(isDashboardHref("/login")).toBe(false);
  });

  it("strips organization scope for route authorization and active nav", () => {
    expect(stripOrgScope("/org/acme/dashboard/paging/services")).toBe(
      "/dashboard/paging/services",
    );
  });
});
