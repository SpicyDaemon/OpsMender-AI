/**
 * Workspace branding waits for a temporary password to change (O-18): the
 * API refuses every read but the person's own profile until then, so the
 * provider does not ask for the workspace, and it asks once the change
 * clears the flag, so a branded workspace keeps its title after the change.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  user: null as null | { primary_org_id: string; must_change_password: boolean },
}));
const apiMocks = vi.hoisted(() => ({ getOrganization: vi.fn() }));

vi.mock("@/context/auth", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("@/lib/api", () => ({ getOrganization: apiMocks.getOrganization }));

import { BrandingProvider } from "@/context/branding";

beforeEach(() => {
  vi.clearAllMocks();
  document.title = "OpsMender";
  apiMocks.getOrganization.mockResolvedValue({
    id: "org-1",
    branding: { company_name: "Acme", favicon_url: null },
  });
});

describe("BrandingProvider", () => {
  it("waits for a temporary password to change, then loads the branding", async () => {
    state.user = { primary_org_id: "org-1", must_change_password: true };
    const view = render(
      <BrandingProvider>
        <span>page</span>
      </BrandingProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.getOrganization).not.toHaveBeenCalled();
    expect(document.title).toBe("OpsMender");

    state.user = { primary_org_id: "org-1", must_change_password: false };
    view.rerender(
      <BrandingProvider>
        <span>page</span>
      </BrandingProvider>,
    );
    await waitFor(() => expect(document.title).toBe("Acme | OpsMender"));
    expect(apiMocks.getOrganization).toHaveBeenCalledTimes(1);
    expect(apiMocks.getOrganization).toHaveBeenCalledWith("org-1");
  });
});
