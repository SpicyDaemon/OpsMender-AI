import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("id=u-1"),
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
  getUser: vi.fn(),
  getRosterImpact: vi.fn(),
  getUserDeletePreconditions: vi.fn(),
  mintPasswordReset: vi.fn(),
  setTemporaryPassword: vi.fn(),
  softDeleteUser: vi.fn(),
  updateUser: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import PersonDetailPage from "@/app/dashboard/people/detail/page";

beforeEach(() => {
  apiMocks.getUser.mockResolvedValue({
    id: "u-1",
    username: "ada",
    email: "ada@example.com",
    auth_source: "local",
    role: "operator",
    is_active: true,
    first_name: "Ada",
    last_name: "Lovelace",
    primary_org_id: "org-1",
    created_at: "2026-01-01T00:00:00Z",
    deleted_at: null,
  });
});

const IMPACT = {
  items: [
    {
      roster_id: "r1",
      roster_name: "Primary",
      on_current_shift: true,
      current_shift_taken_by: "grace",
    },
  ],
};
const NOTE =
  " They leave this Roster: Primary (grace takes the current shift). The others keep their shifts.";

describe("People detail confirmations (M1-37)", () => {
  beforeEach(() => {
    apiMocks.updateUser.mockReset();
    apiMocks.getRosterImpact.mockReset();
  });

  it("lists the Rosters a demotion to Viewer takes them off", async () => {
    apiMocks.getRosterImpact.mockResolvedValue(IMPACT);
    apiMocks.updateUser.mockResolvedValue({});
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<PersonDetailPage />);

    const select = (await screen.findByLabelText("Global role")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "viewer" } });
    fireEvent.click(screen.getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(confirmSpy.mock.calls[0][0]).toContain("leave every Roster");
    expect(String(confirmSpy.mock.calls[0][0]).endsWith(NOTE)).toBe(true);
    expect(apiMocks.updateUser).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("lists them before a deactivation too", async () => {
    apiMocks.getRosterImpact.mockResolvedValue(IMPACT);
    apiMocks.updateUser.mockResolvedValue({});
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PersonDetailPage />);

    fireEvent.click(await screen.findByRole("button", { name: /deactivate/i }));

    await waitFor(() =>
      expect(apiMocks.updateUser).toHaveBeenCalledWith("u-1", { is_active: false }),
    );
    expect(String(confirmSpy.mock.calls[0][0]).endsWith(NOTE)).toBe(true);
    confirmSpy.mockRestore();
  });

  it("changes nothing when the Rosters can't be listed", async () => {
    apiMocks.getRosterImpact.mockRejectedValue(new Error("Network down"));
    apiMocks.updateUser.mockResolvedValue({});
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PersonDetailPage />);

    fireEvent.click(await screen.findByRole("button", { name: /deactivate/i }));

    await waitFor(() => expect(apiMocks.getRosterImpact).toHaveBeenCalled());
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(apiMocks.updateUser).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

describe("People detail page", () => {
  it("uses the display name in the header and shows Joined in the summary", async () => {
    render(<PersonDetailPage />);

    expect(await screen.findByRole("heading", { name: "Ada Lovelace" })).toBeTruthy();
    expect(screen.getAllByText("ada@example.com").length).toBeGreaterThan(0);

    await waitFor(() => expect(screen.getByText("Joined")).toBeTruthy());
  });
});
