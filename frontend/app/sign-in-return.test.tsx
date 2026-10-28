/**
 * Every way of signing in returns to a signed action link's confirm page when
 * the link sent the person to sign in (M1-99), and to the dashboard otherwise.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const LINK = "/actions/confirm#token=abc.def";
const KEY = "opsmender_return_to";

const navigate = vi.hoisted(() => vi.fn());
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const auth = vi.hoisted(() => ({ login: vi.fn(), refresh: vi.fn() }));
const apiMocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  getRegistrationOpen: vi.fn(),
  resolveTenant: vi.fn(),
  setToken: vi.fn(),
  verifyMFA: vi.fn(),
  changeMyPassword: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/use-dashboard-navigation", () => ({
  useDashboardNavigation: () => navigate,
}));
vi.mock("@/lib/api", () => apiMocks);
// A refresh after the password change clears the flag, as the real one does.
vi.mock("@/context/auth", async () => {
  const React = await import("react");
  return {
    useAuth: () => {
      const [user, setUser] = React.useState({
        id: "u-1",
        username: "ada",
        must_change_password: true,
      });
      return {
        login: auth.login,
        user,
        loading: false,
        refresh: async () => {
          await auth.refresh();
          setUser((current) => ({ ...current, must_change_password: false }));
        },
      };
    },
  };
});
vi.mock("@/components/auth/AuthShell", () => ({
  AuthShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/MFASettings", () => ({
  MFASettings: ({ onEnabled }: { onEnabled: () => void }) => (
    <button type="button" onClick={() => onEnabled()}>
      Turn on MFA
    </button>
  ),
}));

import LoginPage from "@/app/login/page";
import MFAChallengePage from "@/app/mfa-challenge/page";
import MFASetupPage from "@/app/mfa-setup/page";
import PasswordChangeRequiredPage from "@/app/password-change-required/page";

const realLocation = window.location;
let assigned: string[] = [];

function stubLocation(pathname: string, hash = "") {
  assigned = [];
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      pathname,
      hash,
      search: "",
      get href() {
        return assigned.at(-1) ?? `http://localhost${pathname}${hash}`;
      },
      set href(value: string) {
        assigned.push(value);
      },
    },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  navigate.mockReset();
  router.push.mockReset();
  router.replace.mockReset();
  auth.login.mockReset();
  auth.refresh.mockReset().mockResolvedValue(undefined);
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.getRegistrationOpen.mockResolvedValue({ open: false });
  apiMocks.resolveTenant.mockResolvedValue(null);
  apiMocks.getMe.mockResolvedValue({ id: "u-1" });
  apiMocks.verifyMFA.mockResolvedValue({ access_token: "jwt" });
  apiMocks.changeMyPassword.mockResolvedValue(undefined);
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

async function signInWithPassword() {
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "ada@example.com" } });
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "s3cret-pass" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("signing in comes back to an action link (M1-99)", () => {
  it("after a password sign-in", async () => {
    stubLocation("/login");
    sessionStorage.setItem(KEY, LINK);
    auth.login.mockResolvedValue({});
    await signInWithPassword();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(LINK));
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("goes to the dashboard when no link asked", async () => {
    stubLocation("/login");
    auth.login.mockResolvedValue({});
    await signInWithPassword();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/dashboard"));
  });

  it("keeps the link through the MFA challenge", async () => {
    stubLocation("/login");
    sessionStorage.setItem(KEY, LINK);
    auth.login.mockResolvedValue({ mfaRequired: true, mfaToken: "mfa-1" });
    await signInWithPassword();
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/mfa-challenge"));
    expect(sessionStorage.getItem(KEY)).toBe(LINK);

    stubLocation("/mfa-challenge");
    render(<MFAChallengePage />);
    fireEvent.change(screen.getByLabelText(/Authenticator code/), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(assigned).toEqual([LINK]));
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("after an SSO handoff", async () => {
    stubLocation("/login", "#sso_token=jwt-from-sso");
    sessionStorage.setItem(KEY, LINK);
    render(<LoginPage />);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(LINK));
    expect(apiMocks.setToken).toHaveBeenCalledWith("jwt-from-sso");
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("after MFA setup", async () => {
    stubLocation("/mfa-setup");
    sessionStorage.setItem(KEY, LINK);
    render(<MFASetupPage />);
    fireEvent.click(screen.getByRole("button", { name: "Turn on MFA" }));
    await waitFor(() => expect(assigned).toEqual([LINK]));
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("after a forced password change", async () => {
    // The API client remembers the link when it sends the confirm page to
    // the password change page (frontend/lib/api.test.ts).
    stubLocation("/password-change-required");
    sessionStorage.setItem(KEY, LINK);
    render(<PasswordChangeRequiredPage />);
    fireEvent.change(screen.getByLabelText(/Current \(temporary\) password/), {
      target: { value: "temporary-1" },
    });
    fireEvent.change(screen.getByLabelText(/^New password/), {
      target: { value: "a-new-password" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm new password/), {
      target: { value: "a-new-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: /update password & continue/i }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(LINK));
    // The cleared flag re-renders the page; it must not send the person on
    // to the dashboard instead.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(navigate.mock.calls.map((call) => call[0])).toEqual([LINK]);
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });
});
