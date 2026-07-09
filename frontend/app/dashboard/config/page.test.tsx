import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "u-admin", username: "admin", role: "admin", primary_org_id: "org-1" },
  }),
}));

const apiMocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

vi.mock("@/components/config/ConfigSections", () => ({
  ConfigPageSkeleton: () => <div>Loading settings</div>,
  RetentionSection: () => <section>Retention settings</section>,
  TierSection: () => <section>Tier settings</section>,
  WorkflowSettingsSection: () => <section>Session workflow</section>,
}));

vi.mock("@/components/config/ApiTokensSection", () => ({
  ApiTokensSection: () => <section>API token settings</section>,
}));

vi.mock("@/components/EmailSettingsSection", () => ({
  EmailSettingsSection: () => <section>Email settings</section>,
}));

vi.mock("@/components/VoiceSettingsSection", () => ({
  VoiceSettingsSection: () => <section>Voice settings</section>,
}));

vi.mock("@/components/OrganizationSettingsSection", () => ({
  OrganizationSettingsSection: () => <section>Organization settings</section>,
}));

vi.mock("@/components/StatusPageSettingsSection", () => ({
  StatusPageSettingsSection: () => <section>Status page settings</section>,
}));

import ConfigPage from "@/app/dashboard/config/page";

beforeEach(() => {
  apiMocks.getConfig.mockResolvedValue({});
});

describe("Settings page", () => {
  it("renders the actionable settings pointer cards", async () => {
    render(<ConfigPage />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy());

    const expectedLinks = [
      ["Models", "/dashboard/models"],
      ["MCP servers", "/dashboard/mcp-servers"],
      ["Notification Channels", "/dashboard/paging/notification-channels"],
    ] as const;

    for (const [name, href] of expectedLinks) {
      const link = screen.getByRole("link", { name: new RegExp(name, "i") });
      expect(link.getAttribute("href")).toBe(href);
    }

    expect(screen.queryByRole("heading", { name: "Advanced" })).toBeNull();
    expect(screen.getByText("Session workflow")).toBeTruthy();
    expect(screen.getByText("Voice settings")).toBeTruthy();
    expect(screen.getByText("API token settings")).toBeTruthy();
  });
});
