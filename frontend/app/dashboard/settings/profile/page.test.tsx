/**
 * My profile page — fields render from the current user and a profile
 * save calls the self-update API.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/settings/profile",
}));

const refresh = vi.fn().mockResolvedValue(undefined);
// Stable user reference — a fresh object per call would loop the page's
// init effect (mirrors how the real auth context holds a stable value).
const MOCK_USER = {
  id: "u-1",
  username: "ada",
  email: "ada@test.com",
  role: "operator",
  auth_source: "local",
  is_active: true,
  first_name: "Ada",
  last_name: "Lovelace",
  avatar_color: null,
  primary_org_id: "org-1",
  created_at: "2026-01-01T00:00:00Z",
};
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: MOCK_USER, refresh }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  updateMe: vi.fn().mockResolvedValue({}),
  changeMyPassword: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/lib/api", () => apiMocks);

import ProfileSettingsPage from "@/app/dashboard/settings/profile/page";

beforeEach(() => {
  apiMocks.updateMe.mockClear();
});

describe("My profile page", () => {
  it("renders profile fields populated from the current user", () => {
    render(<ProfileSettingsPage />);
    expect(screen.getByText("My profile")).toBeTruthy();
    expect((screen.getByLabelText("First name") as HTMLInputElement).value).toBe("Ada");
    expect((screen.getByLabelText("Username") as HTMLInputElement).value).toBe("ada");
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("ada@test.com");
  });

  it("saves profile changes via updateMe", async () => {
    render(<ProfileSettingsPage />);
    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "Augusta" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save profile/i }));
    await waitFor(() => expect(apiMocks.updateMe).toHaveBeenCalledTimes(1));
    expect(apiMocks.updateMe.mock.calls[0][0]).toMatchObject({ first_name: "Augusta" });
  });

  it("filters the phone field to digits + a leading plus and saves it", async () => {
    render(<ProfileSettingsPage />);
    const phone = screen.getByLabelText(/Phone/) as HTMLInputElement;
    // Letters and formatting are stripped; the leading "+" is preserved.
    fireEvent.change(phone, { target: { value: "+1 (415) 555-01ab00" } });
    expect(phone.value).toBe("+14155550100");
    fireEvent.click(screen.getByRole("button", { name: /save profile/i }));
    await waitFor(() => expect(apiMocks.updateMe).toHaveBeenCalledTimes(1));
    expect(apiMocks.updateMe.mock.calls[0][0]).toMatchObject({
      phone: "+14155550100",
    });
  });

  it("has a password change section", () => {
    render(<ProfileSettingsPage />);
    expect(screen.getByText(/change password/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /update password/i })).toBeTruthy();
  });
});
