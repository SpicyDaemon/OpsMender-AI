/**
 * Auth context — v1 browser session behavior.
 *
 * Covers the QA-facing contract that a stored token keeps the user logged in
 * across a reload-like initialization, that a successful login does NOT clear
 * auth, and that logout clears the stored browser session.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  setToken: vi.fn(),
  clearToken: vi.fn(),
  setOrgId: vi.fn(),
  clearOrgId: vi.fn(),
  getMe: vi.fn(),
  login: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getToken: apiMocks.getToken,
  setToken: apiMocks.setToken,
  clearToken: apiMocks.clearToken,
  setOrgId: apiMocks.setOrgId,
  clearOrgId: apiMocks.clearOrgId,
  getMe: apiMocks.getMe,
  login: apiMocks.login,
}));

import { AuthProvider, useAuth } from "@/context/auth";

const MOCK_USER = {
  id: "u-1",
  username: "ada",
  email: "ada@test.com",
  role: "admin",
  auth_source: "local",
  is_active: true,
  first_name: "Ada",
  last_name: "Lovelace",
  avatar_color: null,
  primary_org_id: "org-1",
  created_at: "2026-01-01T00:00:00Z",
};

function Probe() {
  const { user, loading, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="state">
        {loading ? "loading" : user ? user.username : "anon"}
      </span>
      <button onClick={() => login("ada", "pw")}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // logout navigates via window.location.href — stub it so jsdom doesn't warn.
  Object.defineProperty(window, "location", {
    value: { href: "" },
    writable: true,
  });
});

describe("AuthProvider", () => {
  it("re-hydrates the user from a stored token on init (reload persists)", async () => {
    apiMocks.getToken.mockReturnValue("stored-token");
    apiMocks.getMe.mockResolvedValue(MOCK_USER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("ada"),
    );
    // A valid stored session must NOT be cleared during initialization.
    expect(apiMocks.clearToken).not.toHaveBeenCalled();
  });

  it("stays anonymous (no crash) when there is no stored token", async () => {
    apiMocks.getToken.mockReturnValue(null);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("anon"),
    );
    expect(apiMocks.getMe).not.toHaveBeenCalled();
  });

  it("login persists the token and does not clear auth", async () => {
    apiMocks.getToken.mockReturnValue(null);
    apiMocks.login.mockResolvedValue({ access_token: "new-token", token_type: "bearer" });
    apiMocks.getMe.mockResolvedValue(MOCK_USER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("anon"),
    );

    fireEvent.click(screen.getByText("login"));

    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("ada"),
    );
    expect(apiMocks.setToken).toHaveBeenCalledWith("new-token");
    expect(apiMocks.clearToken).not.toHaveBeenCalled();
  });

  it("returns an MFA challenge without storing a partial-session token", async () => {
    apiMocks.getToken.mockReturnValue(null);
    apiMocks.login.mockResolvedValue({
      access_token: null,
      token_type: "bearer",
      mfa_required: true,
      mfa_token: "challenge-token",
      mfa_enrollment_required: false,
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("anon"),
    );

    fireEvent.click(screen.getByText("login"));

    await waitFor(() => expect(apiMocks.login).toHaveBeenCalledTimes(1));
    expect(apiMocks.setToken).not.toHaveBeenCalled();
    expect(apiMocks.getMe).not.toHaveBeenCalled();
    expect(screen.getByTestId("state").textContent).toBe("anon");
  });

  it("logout clears the stored browser session", async () => {
    apiMocks.getToken.mockReturnValue("stored-token");
    apiMocks.getMe.mockResolvedValue(MOCK_USER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("ada"),
    );

    fireEvent.click(screen.getByText("logout"));

    expect(apiMocks.clearToken).toHaveBeenCalledTimes(1);
    expect(apiMocks.clearOrgId).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.getByTestId("state").textContent).toBe("anon"),
    );
  });
});
