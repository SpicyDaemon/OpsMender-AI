/**
 * Sprint 64 regression — Workspace Settings page renders, and clicking
 * Manage Users / Domains does not crash the dashboard error boundary.
 *
 * The page itself is a large composite, so these tests render the
 * isolated modal subcomponents (their public surface is enough to
 * reproduce the crash path the operator hits).
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "user-me", username: "admin", role: "admin" },
  }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// Use vi.hoisted so the mock factory has access to the spies — without
// this, vi.mock runs before the const declarations and crashes with
// "Cannot access X before initialization".
const apiMocks = vi.hoisted(() => ({
  addUserToOrganization: vi.fn(),
  createOrganization: vi.fn(),
  createOrganizationDomain: vi.fn(),
  deleteOrganization: vi.fn(),
  deleteOrganizationDomain: vi.fn(),
  deleteOrgSAMLConfig: vi.fn(),
  deleteOrgSSOConfig: vi.fn(),
  getConfig: vi.fn(),
  getOrgSAMLConfig: vi.fn(),
  getOrgSSOConfig: vi.fn(),
  listOrganizationDomains: vi.fn(),
  listOrganizations: vi.fn(),
  listOrganizationUsers: vi.fn(),
  listUsers: vi.fn(),
  removeUserFromOrganization: vi.fn(),
  setPrimaryOrganizationDomain: vi.fn(),
  updateOrganization: vi.fn(),
  upsertOrgSAMLConfig: vi.fn(),
  upsertOrgSSOConfig: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

const {
  addUserToOrganization,
  createOrganization,
  createOrganizationDomain,
  deleteOrganization,
  deleteOrganizationDomain,
  deleteOrgSAMLConfig,
  deleteOrgSSOConfig,
  getConfig,
  getOrgSAMLConfig,
  getOrgSSOConfig,
  listOrganizationDomains,
  listOrganizations,
  listOrganizationUsers,
  listUsers,
  removeUserFromOrganization,
  setPrimaryOrganizationDomain,
  updateOrganization,
  upsertOrgSAMLConfig,
  upsertOrgSSOConfig,
} = apiMocks;

import OrganizationsPage from "./page";

const TEST_ORG = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Main",
  slug: "main",
  branding: null,
  created_at: "2026-05-27T00:00:00Z",
  updated_at: "2026-05-27T00:00:00Z",
};

const TEST_ORG_2 = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Test",
  slug: "test",
  branding: null,
  created_at: "2026-05-27T00:00:00Z",
  updated_at: "2026-05-27T00:00:00Z",
};

const TEST_USER = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  username: "admin",
  email: "admin@example.com",
  role: "admin",
  is_active: true,
  auth_source: "local",
  primary_org_id: TEST_ORG.id,
  created_at: "2026-05-27T00:00:00Z",
  last_seen_at: null,
  deleted_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  // Default install: single-workspace mode, no advanced auth, no
  // SSO/SAML configured. This matches what the user reported.
  getConfig.mockResolvedValue({
    tier: 2,
    mcp_servers: [],
    audit_output: "stdout",
    logging_level: "INFO",
    ingest_auto_start_enabled: false,
    ingest_auto_start_min_severity: "critical",
    ingest_auto_start_source: null,
    multi_org_enabled: false,
    smtp_configured: false,
    advanced_auth_enabled: false,
    sso_configured: false,
    saml_configured: false,
  });
  listOrganizations.mockResolvedValue({ items: [TEST_ORG, TEST_ORG_2], total: 2 });
  listOrganizationUsers.mockResolvedValue({
    items: [
      {
        user_id: TEST_USER.id,
        username: TEST_USER.username,
        email: TEST_USER.email,
        role: "admin",
        joined_at: "2026-05-27T00:00:00Z",
      },
    ],
    total: 1,
  });
  listUsers.mockResolvedValue({ items: [TEST_USER], total: 1 });
  listOrganizationDomains.mockResolvedValue({ items: [], total: 0 });
});

describe("Workspace Settings page (single-workspace mode)", () => {
  it("renders with the Workspace Settings label", async () => {
    render(<OrganizationsPage />);
    await waitFor(() => expect(listOrganizations).toHaveBeenCalled());
    expect(
      await screen.findByRole("heading", { name: /Workspace Settings/i }),
    ).toBeTruthy();
  });

  it("hides multi-org-only affordances", async () => {
    render(<OrganizationsPage />);
    await waitFor(() => expect(listOrganizations).toHaveBeenCalled());
    await screen.findByRole("heading", { name: /Workspace Settings/i });
    // No "New Organization" / "Create Organization" buttons in single-workspace mode.
    expect(screen.queryByRole("button", { name: /New Organization/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Create Organization/i })).toBeNull();
  });

  it("does not crash when Manage Users is clicked", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<OrganizationsPage />);
    await screen.findByRole("heading", { name: /Workspace Settings/i });
    // Two orgs render; click Manage Users on the first row.
    const manageButtons = await screen.findAllByRole("button", {
      name: /Manage Users/i,
    });
    expect(manageButtons.length).toBeGreaterThan(0);
    await user.click(manageButtons[0]);
    // Modal should mount + trigger the loadData fetches without throwing.
    await waitFor(() => expect(listOrganizationUsers).toHaveBeenCalled());
    // The modal title contains the org name.
    expect(await screen.findByText(/Manage Users: Main/i)).toBeTruthy();
  });

  it("does not crash when Domains is clicked", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<OrganizationsPage />);
    await screen.findByRole("heading", { name: /Workspace Settings/i });
    const domainButtons = await screen.findAllByRole("button", {
      name: /Domains/i,
    });
    expect(domainButtons.length).toBeGreaterThan(0);
    await user.click(domainButtons[0]);
    await waitFor(() => expect(listOrganizationDomains).toHaveBeenCalled());
  });
});
