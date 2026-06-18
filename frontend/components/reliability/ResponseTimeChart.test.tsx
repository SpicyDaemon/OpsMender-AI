import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ResponseTimeChart } from "@/components/reliability/ResponseTimeChart";
import type { ResponseTimeSeriesPoint } from "@/lib/types";

const SERIES: ResponseTimeSeriesPoint[] = [
  {
    ts: "2026-06-01T00:00:00Z",
    avg_latency_ms: 150,
    min_latency_ms: 100,
    max_latency_ms: 220,
    samples: 5,
  },
  {
    ts: "2026-06-01T00:05:00Z",
    avg_latency_ms: 180,
    min_latency_ms: 120,
    max_latency_ms: 260,
    samples: 5,
  },
];

describe("ResponseTimeChart", () => {
  it("renders the average line and min-max band", () => {
    const { container } = render(
      <ResponseTimeChart series={SERIES} windowValue="24h" />,
    );
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/minimum to maximum/i);
    expect(container.querySelectorAll("path")).toHaveLength(2);
  });

  it("shows an empty state when no latency was recorded", () => {
    render(<ResponseTimeChart series={[]} windowValue="24h" />);
    expect(screen.getByText(/no response-time data/i)).toBeTruthy();
  });
});
