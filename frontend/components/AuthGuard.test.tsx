import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { takeReturnPath } from "@/lib/return-path";

const auth = vi.hoisted(() => ({
  user: null as null | { must_change_password?: boolean; mfa_enrollment_required?: boolean },
  loading: false,
  push: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: auth.push }) }));
vi.mock("@/context/auth", () => ({ useAuth: () => auth }));
import { AuthGuard } from "./AuthGuard";

const TARGET = "/dashboard/incidents/detail?id=00000000-0000-4000-8000-000000000001&from=slack";

beforeEach(() => {
  auth.user = null;
  auth.loading = false;
  auth.push.mockClear();
  sessionStorage.clear();
  const url = new URL(TARGET, "http://localhost");
  Object.defineProperty(window, "location", {
    configurable: true, writable: true,
    value: { pathname: url.pathname, search: url.search, hash: url.hash },
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("AuthGuard detail return", () => {
  it.each([
    { user: null, destination: "/login" },
    { user: { must_change_password: true }, destination: "/password-change-required" },
    { user: { mfa_enrollment_required: true }, destination: "/mfa-setup" },
  ])("retains the requested detail before redirecting to $destination", ({ user, destination }) => {
    auth.user = user;
    render(<AuthGuard><div>Private incident</div></AuthGuard>);
    expect(auth.push).toHaveBeenCalledWith(destination);
    expect(takeReturnPath()).toBe(TARGET);
    expect(screen.queryByText("Private incident")).toBeNull();
  });

  it("does not redirect while identity is loading", () => {
    auth.loading = true;
    render(<AuthGuard><div>Private incident</div></AuthGuard>);
    expect(auth.push).not.toHaveBeenCalled();
    expect(takeReturnPath()).toBe("/dashboard");
  });

  it("renders an authorized identity without leaving a stale return path", () => {
    auth.user = {};
    render(<AuthGuard><div>Private incident</div></AuthGuard>);
    expect(screen.getByText("Private incident")).toBeTruthy();
    expect(auth.push).not.toHaveBeenCalled();
    expect(takeReturnPath()).toBe("/dashboard");
  });
});
