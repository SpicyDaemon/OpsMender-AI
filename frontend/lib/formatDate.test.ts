import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatRelative, formatTime } from "./formatDate";

describe("formatDate helpers", () => {
  const at = new Date("2026-06-24T23:02:00Z");

  it("returns empty string for null/undefined/invalid", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDate("not a date")).toBe("");
    expect(formatDateTime(null)).toBe("");
    expect(formatTime(undefined)).toBe("");
    expect(formatRelative(null)).toBe("");
  });

  it("accepts Date, ISO string, and epoch ms", () => {
    expect(formatDate(at)).toBe(formatDate(at.toISOString()));
    expect(formatDate(at)).toBe(formatDate(at.getTime()));
  });

  it("formats a date with month/day/year and no seconds in datetime", () => {
    // Locale-agnostic assertions: year present, no seconds token.
    expect(formatDate(at)).toContain("2026");
    expect(formatDateTime(at)).toContain("2026");
    expect(formatDateTime(at)).not.toMatch(/:\d{2}:\d{2}/); // no HH:MM:SS
    expect(formatTime(at)).not.toMatch(/:\d{2}:\d{2}/);
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-07-03T12:00:00Z");

  it("says 'just now' under 45s", () => {
    expect(formatRelative(new Date(now.getTime() - 10_000), now)).toBe("just now");
  });

  it("pluralizes minutes and hours", () => {
    expect(formatRelative(new Date(now.getTime() - 60_000), now)).toBe("1 minute ago");
    expect(formatRelative(new Date(now.getTime() - 5 * 60_000), now)).toBe("5 minutes ago");
    expect(formatRelative(new Date(now.getTime() - 60 * 60_000), now)).toBe("1 hour ago");
    expect(formatRelative(new Date(now.getTime() - 3 * 60 * 60_000), now)).toBe("3 hours ago");
  });

  it("reports days up to a week, then falls back to an absolute date", () => {
    expect(formatRelative(new Date(now.getTime() - 2 * 24 * 3600_000), now)).toBe("2 days ago");
    // 215h ≈ 9 days → absolute date, not "215 hours ago" or "9 days ago".
    const nineDays = new Date(now.getTime() - 215 * 3600_000);
    expect(formatRelative(nineDays, now)).toBe(formatDate(nineDays));
  });

  it("handles future instants", () => {
    expect(formatRelative(new Date(now.getTime() + 5 * 60_000), now)).toBe("in 5 minutes");
  });
});
