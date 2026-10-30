import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  forgetReturnPath,
  isReturnPath,
  rememberReturnPath,
  takeReturnPath,
} from "./return-path";

describe("return path after sign-in (M1-99)", () => {
  beforeEach(() => sessionStorage.clear());

  it("accepts only the confirm page and its fragment", () => {
    expect(isReturnPath("/actions/confirm")).toBe(true);
    expect(isReturnPath("/actions/confirm#token=abc.def")).toBe(true);
    for (const path of [
      "//evil.example.com",
      "https://evil.example.com/actions/confirm",
      "/actions/confirmed",
      "/actions/confirm/../../dashboard",
      "/actions/confirm?next=//evil.example.com",
      "/dashboard",
      "",
    ]) {
      expect(isReturnPath(path)).toBe(false);
    }
  });

  it("gives the remembered path once, then the dashboard", () => {
    rememberReturnPath("/actions/confirm#token=abc.def");
    expect(takeReturnPath()).toBe("/actions/confirm#token=abc.def");
    expect(takeReturnPath()).toBe("/dashboard");
  });

  it("never remembers or returns another path", () => {
    rememberReturnPath("https://evil.example.com");
    expect(takeReturnPath()).toBe("/dashboard");
    sessionStorage.setItem("opsmender_return_to", "//evil.example.com");
    expect(takeReturnPath()).toBe("/dashboard");
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("forgets the path once the link is open", () => {
    rememberReturnPath("/actions/confirm#token=abc.def");
    forgetReturnPath();
    expect(takeReturnPath()).toBe("/dashboard");
  });
});

describe("incident and session return paths (M1-115)", () => {
  const id = "00000000-0000-4000-8000-000000000001";
  beforeEach(() => sessionStorage.clear());
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each([
    `/dashboard/incidents/detail?id=${id}`,
    `/dashboard/incidents/detail?id=${id}&from=slack`,
    `/dashboard/sessions/detail?id=${id}`,
    `/dashboard/sessions/detail?from=teams&id=${id}`,
  ])("returns the requested detail once: %s", (path) => {
    expect(isReturnPath(path)).toBe(true);
    rememberReturnPath(path);
    expect(takeReturnPath()).toBe(path);
    expect(takeReturnPath()).toBe("/dashboard");
  });

  it.each([
    `https://evil.example/dashboard/incidents/detail?id=${id}`,
    `//evil.example/dashboard/incidents/detail?id=${id}`,
    `/dashboard/incidents/../detail?id=${id}`,
    `/dashboard/%69ncidents/detail?id=${id}`,
    `/dashboard/incidents/detail?id=${id}#token=secret`,
    `/dashboard/incidents/detail?id=${id}&next=//evil.example`,
    `/dashboard/incidents/detail?id=${id}&id=${id}`,
    `/dashboard/incidents/detail?id=${id}&from=slack&from=teams`,
    `/dashboard/incidents/detail?id=${id}&from=https%3A%2F%2Fevil.example`,
    `/dashboard/incidents/detail?id=${id}&token=secret`,
    `/dashboard/incidents/detail?id=%2f%2fevil.example`,
    "/dashboard/incidents/detail?id=not-a-uuid",
    "/dashboard/incidents/detail",
    `/dashboard/incidents/detail/?id=${id}`,
    ` /dashboard/incidents/detail?id=${id}`,
    `\\dashboard\\incidents\\detail?id=${id}`,
    `javascript:/dashboard/incidents/detail?id=${id}`,
  ])("refuses unsafe or unsupported details: %s", (path) => {
    expect(isReturnPath(path)).toBe(false);
    sessionStorage.setItem("opsmender_return_to", path);
    expect(takeReturnPath()).toBe("/dashboard");
  });

  it("falls back if session storage cannot be read", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => { throw new Error("Storage blocked"); },
    });
    expect(takeReturnPath()).toBe("/dashboard");
  });
});
