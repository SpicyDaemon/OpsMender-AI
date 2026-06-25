import { describe, expect, it } from "vitest";
import { scopeDashboardPath, stripOrgScope } from "./org-path";

describe("organization-scoped dashboard paths", () => {
  it("adds the organization slug to dashboard paths", () => {
    expect(scopeDashboardPath("/dashboard/incidents", "acme")).toBe(
      "/org/acme/dashboard/incidents",
    );
  });

  it("strips organization scope for route authorization and active nav", () => {
    expect(stripOrgScope("/org/acme/dashboard/paging/services")).toBe(
      "/dashboard/paging/services",
    );
  });
});
