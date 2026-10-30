import React, { type ReactNode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { rememberReturnPath } from "@/lib/return-path";

const flow = vi.hoisted(() => ({
  router: { push: vi.fn(), replace: vi.fn() },
  user: null as null | { must_change_password?: boolean; mfa_enrollment_required?: boolean },
  login: vi.fn(), refresh: vi.fn(),
  getMe: vi.fn(), getRegistrationOpen: vi.fn(), resolveTenant: vi.fn(),
  setToken: vi.fn(), verifyMFA: vi.fn(), changeMyPassword: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => flow.router }));
vi.mock("@/context/auth", () => ({ useAuth: () => ({ ...flow, loading: false }) }));
vi.mock("@/components/auth/AuthShell", () => ({ AuthShell: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/MFASettings", () => ({
  MFASettings: ({ onEnabled }: { onEnabled: () => Promise<void> }) => <button onClick={() => void onEnabled()}>Finish mock enrollment</button>,
}));
vi.mock("@/lib/api", () => ({
  getMe: flow.getMe, getRegistrationOpen: flow.getRegistrationOpen,
  resolveTenant: flow.resolveTenant, setToken: flow.setToken,
  verifyMFA: flow.verifyMFA, changeMyPassword: flow.changeMyPassword,
}));

import LoginPage from "@/app/login/page";
import MFAChallengePage from "@/app/mfa-challenge/page";
import MFASetupPage from "@/app/mfa-setup/page";
import PasswordChangeRequiredPage from "@/app/password-change-required/page";

const INCIDENT = "/dashboard/incidents/detail?id=00000000-0000-4000-8000-000000000001&from=slack";
const SESSION = "/dashboard/sessions/detail?id=00000000-0000-4000-8000-000000000002";

function locationAt(path: string) {
  const url = new URL(path, "http://localhost");
  Object.defineProperty(window, "location", { configurable: true, writable: true,
    value: { href: url.href, pathname: url.pathname, search: url.search, hash: url.hash },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  flow.user = null;
  flow.refresh.mockResolvedValue(undefined);
  flow.getMe.mockResolvedValue({});
  flow.getRegistrationOpen.mockResolvedValue({ open: false });
  flow.resolveTenant.mockResolvedValue({ sso_enabled: false, saml_enabled: false });
  flow.login.mockResolvedValue({ mfaRequired: false, mfaEnrollmentRequired: false });
  flow.verifyMFA.mockResolvedValue({ access_token: "unit-session" });
  flow.changeMyPassword.mockResolvedValue(undefined);
  locationAt("/login");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("detail return through successful authentication", () => {
  it("password sign-in returns to the requested incident once", async () => {
    rememberReturnPath(INCIDENT);
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "synthetic@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "existing-test-password" } });
    fireEvent.click(screen.getByRole("button", { name: /Sign in/i }));
    await waitFor(() => expect(flow.router.push).toHaveBeenCalledWith(INCIDENT));
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("SSO callback clears its token fragment and returns to the requested session", async () => {
    rememberReturnPath(SESSION);
    locationAt("/login#sso_token=unit-sso-session");
    const clearFragment = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
    render(<LoginPage />);
    await waitFor(() => expect(flow.router.push).toHaveBeenCalledWith(SESSION));
    expect(flow.setToken).toHaveBeenCalledWith("unit-sso-session");
    expect(clearFragment).toHaveBeenCalledWith(null, "", "/login");
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("MFA challenge consumes the destination only after verification", async () => {
    rememberReturnPath(SESSION);
    sessionStorage.setItem("opsmender_mfa_token", "unit-challenge");
    locationAt("/mfa-challenge");
    render(<MFAChallengePage />);
    expect(sessionStorage.getItem("opsmender_return_to")).toBe(SESSION);
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(window.location.href).toBe(SESSION));
    expect(flow.verifyMFA).toHaveBeenCalledWith({ mfa_token: "unit-challenge", totp_code: "123456" });
    expect(sessionStorage.getItem("opsmender_mfa_token")).toBeNull();
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("MFA setup returns after the enrollment completion callback", async () => {
    rememberReturnPath(SESSION);
    flow.user = { mfa_enrollment_required: true };
    locationAt("/mfa-setup");
    render(<MFASetupPage />);
    fireEvent.click(screen.getByRole("button", { name: "Finish mock enrollment" }));
    await waitFor(() => expect(window.location.href).toBe(SESSION));
    expect(flow.refresh).toHaveBeenCalled();
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("required password change returns after successful save and identity refresh", async () => {
    rememberReturnPath(INCIDENT);
    flow.user = { must_change_password: true };
    locationAt("/password-change-required");
    render(<PasswordChangeRequiredPage />);
    fireEvent.change(screen.getByLabelText(/Current \(temporary\) password/), { target: { value: "temporary-test-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "replacement-test-password" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "replacement-test-password" } });
    fireEvent.click(screen.getByRole("button", { name: /Update password & continue/ }));
    await waitFor(() => expect(flow.router.push).toHaveBeenCalledWith(INCIDENT));
    expect(flow.refresh).toHaveBeenCalled();
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });
});
