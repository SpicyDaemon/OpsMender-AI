/**
 * People page v1 cleanup — wide layout, Services-style filter bar, direct
 * user creation, and auth-method gating (SSO/SAML hidden unless advanced auth).
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/people",
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "u-admin", username: "admin", role: "admin", primary_org_id: "org-1" },
  }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  createInvite: vi.fn(),
  createUser: vi.fn(),
  getConfig: vi.fn(),
  listInvites: vi.fn(),
  listUsers: vi.fn(),
  resendInvite: vi.fn(),
  revokeInvite: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import PeoplePage from "@/app/dashboard/people/page";

const USER = {
  id: "u-1",
  username: "jdoe",
  email: "jdoe@test.com",
  auth_source: "local",
  role: "operator" as const,
  is_active: true,
  primary_org_id: "org-1",
  created_at: "2026-01-01T00:00:00Z",
  deleted_at: null,
};

beforeEach(() => {
  apiMocks.listUsers.mockResolvedValue({ items: [USER], total: 1 });
  apiMocks.listInvites.mockResolvedValue({ items: [], total: 0 });
  apiMocks.getConfig.mockResolvedValue({
    advanced_auth_enabled: false,
    sso_configured: false,
    saml_configured: false,
  });
});

describe("People page (v1)", () => {
  it("uses a full-width layout (no centered max-width wrapper)", async () => {
    const { container } = render(<PeoplePage />);
    await waitFor(() => expect(screen.getAllByText("jdoe").length).toBeGreaterThan(0));
    expect(container.querySelector(".max-w-6xl")).toBeNull();
    expect((container.firstChild as HTMLElement)?.className).toContain("w-full");
  });

  it("renders a Services-style filter bar (search) and a direct New user action", async () => {
    render(<PeoplePage />);
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/search by name, username, or email/i)).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: /new user/i })).toBeTruthy();
  });

  it("shows the Joined / Sent column in the unified table", async () => {
    render(<PeoplePage />);
    await waitFor(() => expect(screen.getAllByText("jdoe").length).toBeGreaterThan(0));
    expect(screen.getAllByText(/joined \/ sent/i).length).toBeGreaterThan(0);
  });

  it("has no separate Invites tab (unified People table)", async () => {
    render(<PeoplePage />);
    await waitFor(() => expect(screen.getAllByText("jdoe").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: /^invites$/i })).toBeNull();
  });

  it("shows pending invites as Invited rows in the same table", async () => {
    apiMocks.listInvites.mockResolvedValue({
      items: [
        {
          id: "inv-1",
          org_id: "org-1",
          email: "invitee@test.com",
          role: "viewer",
          status: "pending",
          expires_at: "2026-07-01T00:00:00Z",
          created_at: "2026-06-01T00:00:00Z",
        },
      ],
      total: 1,
    });
    render(<PeoplePage />);
    await waitFor(() =>
      expect(screen.getAllByText("invitee@test.com").length).toBeGreaterThan(0),
    );
    expect(screen.getAllByText("Invited").length).toBeGreaterThan(0);
  });

  it("hides the Auth method column when advanced auth is disabled", async () => {
    render(<PeoplePage />);
    await waitFor(() => expect(screen.getAllByText("jdoe").length).toBeGreaterThan(0));
    expect(screen.queryByText(/auth method/i)).toBeNull();
  });

  it("shows the Auth method column when advanced auth is enabled", async () => {
    apiMocks.getConfig.mockResolvedValue({
      advanced_auth_enabled: true,
      sso_configured: false,
      saml_configured: false,
    });
    render(<PeoplePage />);
    await waitFor(() => expect(screen.getAllByText("jdoe").length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(screen.getAllByText(/auth method/i).length).toBeGreaterThan(0),
    );
  });
});
