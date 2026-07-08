import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/Toast";
import { ApiTokensSection } from "@/components/config/ApiTokensSection";
import type { ApiTokenResponse } from "@/lib/types";

const apiMocks = vi.hoisted(() => ({
  createApiToken: vi.fn(),
  listApiTokens: vi.fn(),
  revokeApiToken: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

const baseToken: ApiTokenResponse = {
  id: "token-1",
  name: "deploy-script",
  token_prefix: "omk_abcdef12",
  role: "operator",
  created_by: "user-1",
  created_at: "2026-07-08T12:00:00Z",
  last_used_at: null,
  revoked_at: null,
};

function renderSection() {
  return render(
    <ToastProvider>
      <ApiTokensSection />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listApiTokens.mockResolvedValue({ items: [baseToken], total: 1 });
  apiMocks.createApiToken.mockResolvedValue({
    ...baseToken,
    id: "token-2",
    name: "nightly-job",
    token_prefix: "omk_secret1",
    token: "omk_secret123",
  });
  apiMocks.revokeApiToken.mockResolvedValue(undefined);
});

describe("ApiTokensSection", () => {
  it("creates a token, shows the one-time secret, and revokes an existing token", async () => {
    const user = userEvent.setup();
    renderSection();

    await waitFor(() => expect(screen.getAllByText("deploy-script").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Operator").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Never").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /create token/i }));
    await user.type(screen.getByLabelText("Name"), "nightly-job");
    await user.selectOptions(screen.getByLabelText("Role"), "viewer");
    const createButtons = screen.getAllByRole("button", { name: "Create token" });
    await user.click(createButtons[createButtons.length - 1]);

    await waitFor(() =>
      expect(apiMocks.createApiToken).toHaveBeenCalledWith({
        name: "nightly-job",
        role: "viewer",
      }),
    );
    expect(screen.getByDisplayValue("omk_secret123")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.click(
      screen.getAllByRole("button", { name: "Revoke deploy-script" })[0],
    );
    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() =>
      expect(apiMocks.revokeApiToken).toHaveBeenCalledWith("token-1"),
    );
  });
});
