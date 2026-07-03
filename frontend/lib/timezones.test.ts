import { describe, expect, it } from "vitest";

import {
  convertWallTime,
  timeZoneOptionsWithOffset,
  tzOffsetLabel,
  tzOffsetMinutes,
} from "./timezones";

describe("tzOffsetMinutes / tzOffsetLabel", () => {
  it("reports UTC as zero", () => {
    const at = new Date("2026-07-01T12:00:00Z");
    expect(tzOffsetMinutes("UTC", at)).toBe(0);
    expect(tzOffsetLabel("UTC", at)).toBe("+00:00");
  });

  it("reports a positive half-hour offset for Kolkata", () => {
    const at = new Date("2026-07-01T12:00:00Z");
    // India Standard Time is UTC+05:30 year-round.
    expect(tzOffsetMinutes("Asia/Kolkata", at)).toBe(330);
    expect(tzOffsetLabel("Asia/Kolkata", at)).toBe("+05:30");
  });

  it("reports a negative offset for US Central in summer (DST)", () => {
    const at = new Date("2026-07-01T12:00:00Z");
    // CDT is UTC-05:00 during daylight saving.
    expect(tzOffsetLabel("America/Chicago", at)).toBe("-05:00");
  });

  it("falls back to +00:00 for an unknown zone", () => {
    expect(tzOffsetLabel("Not/AZone")).toBe("+00:00");
  });
});

describe("timeZoneOptionsWithOffset", () => {
  it("labels each option with its offset and keeps the current value", () => {
    const opts = timeZoneOptionsWithOffset("UTC");
    const utc = opts.find((o) => o.value === "UTC");
    expect(utc?.label).toBe("UTC (+00:00)");
    // Every label carries a signed offset in parentheses.
    for (const o of opts.slice(0, 20)) {
      expect(o.label).toMatch(/\([+-]\d{2}:\d{2}\)$/);
    }
  });
});

describe("convertWallTime", () => {
  it("is a no-op when the zones match", () => {
    expect(convertWallTime("2026-07-01", "09:00", "UTC", "UTC")).toEqual({
      time: "09:00",
      dayShift: 0,
    });
  });

  it("shifts UTC 09:00 back 5h into US Central (summer)", () => {
    const { time, dayShift } = convertWallTime(
      "2026-07-01",
      "09:00",
      "UTC",
      "America/Chicago",
    );
    expect(time).toBe("04:00");
    expect(dayShift).toBe(0);
  });

  it("rolls to the previous day when the offset crosses midnight", () => {
    // 02:00 UTC in Chicago (CDT, -5) is 21:00 the previous day.
    const { time, dayShift } = convertWallTime(
      "2026-07-01",
      "02:00",
      "UTC",
      "America/Chicago",
    );
    expect(time).toBe("21:00");
    expect(dayShift).toBe(-1);
  });

  it("adds the half-hour for Kolkata", () => {
    const { time, dayShift } = convertWallTime(
      "2026-07-01",
      "09:00",
      "UTC",
      "Asia/Kolkata",
    );
    expect(time).toBe("14:30");
    expect(dayShift).toBe(0);
  });
});
