import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { UptimeStrip } from "@/components/reliability/UptimeStrip";
import type { UptimeSeriesPoint } from "@/lib/types";

const SERIES: UptimeSeriesPoint[] = [
  { ts: "2026-06-01T00:00:00Z", up_pct: 100, status: "up" },
  { ts: "2026-06-01T01:00:00Z", up_pct: 0, status: "down" },
  { ts: "2026-06-01T02:00:00Z", up_pct: 100, status: "unknown" },
];

describe("UptimeStrip", () => {
  it("renders one segment per series point with status data", () => {
    const { container } = render(<UptimeStrip series={SERIES} />);
    const segments = container.querySelectorAll("[data-status]");
    expect(segments).toHaveLength(3);
    expect(segments[0].getAttribute("data-status")).toBe("up");
    expect(segments[1].getAttribute("data-status")).toBe("down");
    expect(segments[2].getAttribute("data-status")).toBe("unknown");
  });

  it("shows an empty-state message when there is no history", () => {
    render(<UptimeStrip series={[]} />);
    expect(screen.getByText(/no uptime history/i)).toBeTruthy();
  });
});
