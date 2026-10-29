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
  const parsed = new URL(pathname, "http://localhost");
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { href: parsed.href, pathname: parsed.pathname, search: parsed.search, hash: parsed.hash },
  });
}

beforeEach(() => {
  sessionStorage.clear();
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
  it.each([
    [401, "/dashboard/incidents/detail?id=00000000-0000-4000-8000-000000000001&from=slack", "/login"],
    [401, "/dashboard/sessions/detail?id=00000000-0000-4000-8000-000000000001", "/login"],
    [403, "/dashboard/incidents/detail?id=00000000-0000-4000-8000-000000000001&from=slack", "/password-change-required"],
    [403, "/dashboard/sessions/detail?id=00000000-0000-4000-8000-000000000001", "/password-change-required"],
  ])("retains a safe detail before auth refusal %s", async (status, path, destination) => {
    setToken("session-token");
    openAt(path as string);
    vi.stubGlobal("fetch", answer(status as number, REFUSAL));
    await expect(api.get("/auth/me")).rejects.toThrow();
    expect(window.location.href).toBe(destination);
    expect(sessionStorage.getItem("opsmender_return_to")).toBe(path);
  });

  it("sends only a dashboard tab to the password change page", async () => {
    setToken("session-token");
    vi.stubGlobal("fetch", answer(403, REFUSAL));

    openAt("/dashboard/incidents");
    await expect(api.get("/incidents")).rejects.toThrow(REFUSAL.detail);
    expect(window.location.href).toBe("/password-change-required");
    // The session stays: the person needs it to change the password.
    expect(getToken()).toBe("session-token");

    // An action link's confirm page needs a full session too, and comes back
    // once the password has changed (M1-99).
    sessionStorage.clear();
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        href: "http://localhost/actions/confirm#token=t.sig",
        pathname: "/actions/confirm",
        hash: "#token=t.sig",
      },
    });
    await expect(api.post("/action-links/preview", { token: "t.sig" })).rejects.toThrow(
      REFUSAL.detail,
    );
    expect(window.location.href).toBe("/password-change-required");
    expect(sessionStorage.getItem("opsmender_return_to")).toBe("/actions/confirm#token=t.sig");
    sessionStorage.clear();

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

  it("brings an action link back after a session runs out on it", async () => {
    sessionStorage.clear();
    setToken("expired-session");
    vi.stubGlobal("fetch", answer(401, { detail: "Not authenticated" }));
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        href: "http://localhost/actions/confirm#token=abc.def",
        pathname: "/actions/confirm",
        hash: "#token=abc.def",
      },
    });
    await expect(api.post("/action-links/confirm", { token: "abc.def" })).rejects.toThrow(
      "Unauthorized",
    );
    expect(window.location.href).toBe("/login");
    expect(sessionStorage.getItem("opsmender_return_to")).toBe(
      "/actions/confirm#token=abc.def",
    );

    // Any other page goes to sign-in without a return path.
    sessionStorage.clear();
    setToken("expired-session");
    openAt("/dashboard/incidents");
    await expect(api.get("/incidents")).rejects.toThrow("Unauthorized");
    expect(sessionStorage.getItem("opsmender_return_to")).toBeNull();
  });

  it("turns the list FastAPI sends for a bad body into one message", async () => {
    // A page shows err.message, so a refused save names its reason (M1-59).
    setToken("session-token");
    vi.stubGlobal(
      "fetch",
      answer(422, {
        detail: [
          {
            type: "value_error",
            loc: ["body", "routing"],
            msg: "Value error, routing.P1 must be a list of up to 3 stages",
          },
        ],
      }),
    );
    openAt("/dashboard/paging");
    await expect(
      api.put("/users/me/notification-preferences", {}),
    ).rejects.toThrow("routing.P1 must be a list of up to 3 stages");
  });
});
