import { describe, expect, it } from "vitest";

import { formatUptimePct, formatMtbf, formatDuration } from "@/lib/uptime";

describe("formatUptimePct", () => {
  it("renders 100 as 100%", () => {
    expect(formatUptimePct(100)).toBe("100%");
  });

  it("renders 99.999 correctly and does not round up to 100", () => {
    expect(formatUptimePct(99.999)).toBe("99.999%");
  });

  it("does not round 99.9994 up to 100", () => {
    expect(formatUptimePct(99.9994)).toBe("99.999%");
  });

  it("uses adaptive precision (trims trailing zeros)", () => {
    expect(formatUptimePct(99.9)).toBe("99.9%");
    expect(formatUptimePct(99.99)).toBe("99.99%");
    expect(formatUptimePct(95)).toBe("95.0%");
  });

  it("renders an em dash for null", () => {
    expect(formatUptimePct(null)).toBe("—");
  });
});

describe("formatMtbf", () => {
  it("says 'No downtime' when null (no failures)", () => {
    expect(formatMtbf(null)).toBe("No downtime");
  });

  it("formats large values as days", () => {
    expect(formatMtbf(12.3 * 86400)).toBe("12.3 days");
  });

  it("formats hours", () => {
    expect(formatMtbf(3 * 3600)).toBe("3.0 hours");
  });
});

describe("formatDuration", () => {
  it("formats minutes and zero", () => {
    expect(formatDuration(0)).toBe("0 min");
    expect(formatDuration(120)).toBe("2 min");
    expect(formatDuration(3600)).toBe("1h");
  });
});
