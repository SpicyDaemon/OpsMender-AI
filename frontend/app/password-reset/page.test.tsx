/**
 * Password-reset page renders a set-new-password form from `?token=` and
 * shows a clean message when the token is missing.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const params = { value: "tok-123" };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => (k === "token" ? params.value : null) }),
}));

vi.mock("@/context/theme", () => ({
  useTheme: () => ({ mode: "system", resolvedTheme: "dark", setMode: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({ consumePasswordReset: vi.fn().mockResolvedValue(undefined) }));

import PasswordResetPage from "@/app/password-reset/page";

describe("Password reset page", () => {
  it("renders the new-password form when a token is present", () => {
    params.value = "tok-123";
    render(<PasswordResetPage />);
    expect(screen.getByText(/set a new password/i)).toBeTruthy();
    expect(screen.getByText("New password")).toBeTruthy();
    expect(screen.getByText("Confirm new password")).toBeTruthy();
    expect(screen.getByRole("button", { name: /update password/i })).toBeTruthy();
  });

  it("shows a clean message when the token is missing (no 404)", () => {
    params.value = "";
    render(<PasswordResetPage />);
    expect(screen.getByText(/reset link incomplete/i)).toBeTruthy();
  });
});
