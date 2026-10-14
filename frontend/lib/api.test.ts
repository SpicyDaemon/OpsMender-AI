/**
 * The API client sends a dashboard tab whose person must still change a
 * temporary password to the change page (O-18), and leaves every other page
 * and every other refusal alone.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, getToken, setToken } from "@/lib/api";

const REFUSAL = {
  detail: "Change your temporary password before using OpsMender.",
  code: "password_change_required",
};

function answer(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    json: async () => body,
  });
}

function openAt(pathname: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { href: `http://localhost${pathname}`, pathname },
  });
}

beforeEach(() => {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request", () => {
  it("sends only a dashboard tab to the password change page", async () => {
    setToken("session-token");
    vi.stubGlobal("fetch", answer(403, REFUSAL));

    openAt("/dashboard/incidents");
    await expect(api.get("/incidents")).rejects.toThrow(REFUSAL.detail);
    expect(window.location.href).toBe("/password-change-required");
    // The session stays: the person needs it to change the password.
    expect(getToken()).toBe("session-token");

    // Sign-in and the change page itself stay where they are.
    for (const page of ["/login", "/password-change-required"]) {
      openAt(page);
      await expect(api.get("/organizations/org-1")).rejects.toThrow(
        REFUSAL.detail,
      );
      expect(window.location.href).toBe(`http://localhost${page}`);
    }

    // Any other refusal leaves the dashboard tab alone.
    vi.stubGlobal(
      "fetch",
      answer(403, { detail: "Role 'viewer' not in allowed roles" }),
    );
    openAt("/dashboard/settings");
    await expect(api.get("/config")).rejects.toThrow("not in allowed roles");
    expect(window.location.href).toBe("http://localhost/dashboard/settings");
  });
});
