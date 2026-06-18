"use client";

import { useState } from "react";
import type { UptimeSeriesPoint } from "@/lib/types";

/**
 * Enlarged up/down uptime chart (v1.2). One bar per series bucket with a
 * labeled X axis (dates) and a binary Y axis (Up / Down): up buckets rise to
 * the "Up" line (green), down buckets sit low at the "Down" line (red), no-data
 * buckets show a faint sliver. Hovering a bucket pops a tooltip with the
 * date/time and status. Hand-rolled (no charting dependency).
 */

const PLOT_HEIGHT = 150; // px
const DOWN_LEVEL = 0.3; // down bars rise to 30% of the plot height
const UNKNOWN_LEVEL = 0.08;

function fmtTick(iso: string, windowValue: string): string {
  const d = new Date(iso);
  if (windowValue === "24h") {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (windowValue === "365d" || windowValue === "1y") {
    return d.toLocaleDateString([], { month: "short", year: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function UptimeBarChart({
  series,
  windowValue,
  className = "",
}: {
  series: UptimeSeriesPoint[];
  windowValue: string;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);

  if (!series || series.length === 0) {
    return (
      <div
        className={`flex items-center justify-center rounded-md border border-dashed border-border-subtle text-xs text-fg-muted ${className}`}
        style={{ height: PLOT_HEIGHT }}
      >
        No uptime history yet
      </div>
    );
  }

  // ~6 evenly spaced X-axis tick labels.
  const tickCount = Math.min(6, series.length);
  const tickIdxs = Array.from({ length: tickCount }, (_, i) =>
    Math.round((i * (series.length - 1)) / Math.max(1, tickCount - 1)),
  );

  return (
    <div className={className}>
      <div className="flex">
        {/* Y axis */}
        <div
          className="relative mr-2 w-8 shrink-0 text-[10px] text-fg-muted"
          style={{ height: PLOT_HEIGHT }}
          aria-hidden
        >
          <span className="absolute right-0 top-0 -translate-y-1/2">Up</span>
          <span
            className="absolute right-0 -translate-y-1/2"
            style={{ top: `${(1 - DOWN_LEVEL) * 100}%` }}
          >
            Down
          </span>
        </div>

        {/* Plot */}
        <div className="relative flex-1">
          {/* gridlines */}
          <div className="pointer-events-none absolute inset-0">
            <div className="absolute left-0 right-0 top-0 border-t border-border-subtle" />
            <div
              className="absolute left-0 right-0 border-t border-dashed border-border-subtle"
              style={{ top: `${(1 - DOWN_LEVEL) * 100}%` }}
            />
            <div className="absolute bottom-0 left-0 right-0 border-t border-border-subtle" />
          </div>

          <div
            className="flex items-end gap-px"
            style={{ height: PLOT_HEIGHT }}
            role="img"
            aria-label="Uptime history chart"
          >
            {series.map((point, i) => {
              const level =
                point.status === "up"
                  ? 1
                  : point.status === "down"
                    ? DOWN_LEVEL
                    : UNKNOWN_LEVEL;
              const color =
                point.status === "up"
                  ? "bg-status-success"
                  : point.status === "down"
                    ? "bg-status-critical"
                    : "bg-border-subtle";
              return (
                <div
                  key={i}
                  className="flex h-full flex-1 items-end"
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover((h) => (h === i ? null : h))}
                >
                  <div
                    className={`w-full rounded-t-sm ${color} ${
                      hover === i ? "opacity-100 ring-1 ring-fg-primary/40" : "opacity-90"
                    }`}
                    style={{ height: `${level * 100}%` }}
                  />
                </div>
              );
            })}
          </div>

          {/* Tooltip */}
          {hover !== null && series[hover] && (
            <div
              className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-border-strong bg-bg-elevated px-2 py-1 text-[11px] shadow-lg"
              style={{
                left: `${((hover + 0.5) / series.length) * 100}%`,
                top: -6,
              }}
            >
              <div className="font-medium text-fg-primary">
                {new Date(series[hover].ts).toLocaleString()}
              </div>
              <div
                className={
                  series[hover].status === "down"
                    ? "text-status-critical"
                    : series[hover].status === "up"
                      ? "text-status-success"
                      : "text-fg-muted"
                }
              >
                {series[hover].status === "unknown"
                  ? "No data"
                  : series[hover].status === "down"
                    ? `Down · ${series[hover].up_pct.toFixed(1)}% up`
                    : "Up"}
              </div>
            </div>
          )}

          {/* X axis */}
          <div className="relative mt-1.5 h-4 text-[10px] text-fg-muted">
            {tickIdxs.map((idx) => (
              <span
                key={idx}
                className="absolute -translate-x-1/2 whitespace-nowrap"
                style={{
                  left: `${((idx + 0.5) / series.length) * 100}%`,
                }}
              >
                {fmtTick(series[idx].ts, windowValue)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
