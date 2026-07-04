import { describe, expect, it } from "vitest";

import {
  isStaleActiveSession,
  STALE_ACTIVE_SESSION_MS,
} from "@/lib/sessionFreshness";

describe("session freshness", () => {
  const now = Date.parse("2026-07-04T12:00:00.000Z");

  it("flags active sessions older than the stale threshold", () => {
    expect(
      isStaleActiveSession(
        { started_at: new Date(now - STALE_ACTIVE_SESSION_MS - 1).toISOString() },
        now,
      ),
    ).toBe(true);
  });

  it("keeps recent or malformed session timestamps out of the stale state", () => {
    expect(
      isStaleActiveSession(
        { started_at: new Date(now - STALE_ACTIVE_SESSION_MS + 1).toISOString() },
        now,
      ),
    ).toBe(false);
    expect(isStaleActiveSession({ started_at: "not-a-date" }, now)).toBe(false);
    expect(isStaleActiveSession({ started_at: null }, now)).toBe(false);
  });
});
