"use client";

import type { UptimeSeriesPoint } from "@/lib/types";

/**
 * Lightweight uptime history strip: one segment per series bucket, coloured
 * green (up) / red (down) / gray (no data). Intentionally simple — no latency
 * or response-time charting in v1.
 */
export function UptimeStrip({
  series,
  height = 32,
  className = "",
}: {
  series: UptimeSeriesPoint[];
  height?: number;
  className?: string;
}) {
  if (!series || series.length === 0) {
    return (
      <div
        className={`flex items-center justify-center rounded-md border border-dashed border-border-subtle text-xs text-fg-muted ${className}`}
        style={{ height }}
      >
        No uptime history yet
      </div>
    );
  }

  return (
    <div
      className={`flex w-full items-stretch gap-px overflow-hidden rounded-md ${className}`}
      style={{ height }}
      role="img"
      aria-label="Uptime history"
    >
      {series.map((point, i) => {
        const color =
          point.status === "up"
            ? "bg-status-success"
            : point.status === "down"
              ? "bg-status-critical"
              : "bg-border-subtle";
        const title =
          point.status === "unknown"
            ? `${new Date(point.ts).toLocaleString()} — no data`
            : `${new Date(point.ts).toLocaleString()} — ${point.up_pct.toFixed(2)}% up`;
        return (
          <div
            key={i}
            className={`flex-1 ${color} transition-opacity hover:opacity-80`}
            title={title}
            data-status={point.status}
          />
        );
      })}
    </div>
  );
}
