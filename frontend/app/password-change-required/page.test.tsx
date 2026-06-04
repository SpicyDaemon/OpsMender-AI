/**
 * Forced password-change page renders for a user with a temporary password.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("@/context/theme", () => ({
  useTheme: () => ({ mode: "system", resolvedTheme: "dark", setMode: vi.fn() }),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", username: "newhire", must_change_password: true },
    loading: false,
    refresh: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock("@/lib/api", () => ({ changeMyPassword: vi.fn().mockResolvedValue(undefined) }));

import PasswordChangeRequiredPage from "@/app/password-change-required/page";

describe("Forced password-change page", () => {
  it("prompts for current/new/confirm passwords", () => {
    render(<PasswordChangeRequiredPage />);
    expect(screen.getByText(/set a new password/i)).toBeTruthy();
    expect(screen.getByText(/Current \(temporary\) password/i)).toBeTruthy();
    expect(screen.getByText("New password")).toBeTruthy();
    expect(screen.getByText("Confirm new password")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /update password & continue/i }),
    ).toBeTruthy();
  });
});
