import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./auth";
import { AuthGuard } from "@/components/AuthGuard";
import { getMe, getToken, setToken } from "@/lib/api";
import { forgetReturnPath, takeReturnPath } from "@/lib/return-path";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

function SignOut() {
  const { logout } = useAuth();
  return <button onClick={logout}>Sign out test account</button>;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("intentional sign-out cannot re-save a detail through the guard or a late 401", async () => {
  localStorage.clear();
  sessionStorage.clear();
  const target = "/dashboard/sessions/detail?id=00000000-0000-4000-8000-000000000001";
  Object.defineProperty(window, "location", {
    configurable: true, writable: true,
    // The old document keeps its pathname while full navigation is pending.
    value: { href: `http://localhost${target}`, pathname: "/dashboard/sessions/detail",
      search: "?id=00000000-0000-4000-8000-000000000001", hash: "" },
  });
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    id: "00000000-0000-4000-8000-000000000002", username: "synthetic",
    must_change_password: false, mfa_enrollment_required: false,
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  setToken("unit-signed-in-session");
  render(<AuthProvider><AuthGuard><SignOut /></AuthGuard></AuthProvider>);
  await waitFor(() => expect(screen.getByRole("button", { name: "Sign out test account" })).toBeTruthy());
  expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Sign out test account" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/login"));
  const guardReturn = sessionStorage.getItem("opsmender_return_to");
  expect(getToken()).toBeNull();
  expect(screen.queryByRole("button", { name: "Sign out test account" })).toBeNull();
  expect(window.location.href).toBe("/login");

  // Isolate the API path even if the guard already wrote a value on old code.
  forgetReturnPath();
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 }));
  await expect(getMe()).rejects.toThrow("Unauthorized");
  const lateApiReturn = sessionStorage.getItem("opsmender_return_to");
  expect({ guardReturn, lateApiReturn }).toEqual({ guardReturn: null, lateApiReturn: null });
  expect(takeReturnPath()).toBe("/dashboard");
});
