"use client";

import { useState } from "react";
import type { ResponseTimeSeriesPoint } from "@/lib/types";
import { formatDateTime } from "@/lib/formatDate";

const WIDTH = 1000;
const PLOT_HEIGHT = 180;
const LABEL_HEIGHT = 22;
const HEIGHT = PLOT_HEIGHT + LABEL_HEIGHT;

function formatLatency(value: number | null): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}

function formatTick(iso: string, windowValue: string): string {
  const date = new Date(iso);
  if (["15m", "30m", "1h", "6h", "12h", "24h"].includes(windowValue)) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (windowValue === "365d") {
    return date.toLocaleDateString([], { month: "short", year: "2-digit" });
  }
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function contiguousSegments(series: ResponseTimeSeriesPoint[]) {
  const segments: Array<Array<{ point: ResponseTimeSeriesPoint; index: number }>> = [];
  let current: Array<{ point: ResponseTimeSeriesPoint; index: number }> = [];
  series.forEach((point, index) => {
    if (point.avg_latency_ms == null) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push({ point, index });
    }
  });
  if (current.length) segments.push(current);
  return segments;
}

export function ResponseTimeChart({
  series,
  windowValue,
}: {
  series: ResponseTimeSeriesPoint[];
  windowValue: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const populated = series.filter((point) => point.avg_latency_ms != null);

  if (!populated.length) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-border-subtle text-xs text-fg-muted"
        style={{ height: PLOT_HEIGHT }}
      >
        No response-time data yet
      </div>
    );
  }

  const maxLatency = Math.max(...populated.map((point) => point.avg_latency_ms ?? 0), 1);
  const ceiling = maxLatency * 1.1;
  const x = (index: number) =>
    series.length <= 1 ? WIDTH / 2 : (index / (series.length - 1)) * WIDTH;
  const y = (value: number) => PLOT_HEIGHT - (value / ceiling) * (PLOT_HEIGHT - 8);
  const segments = contiguousSegments(series);
  const tickCount = Math.min(6, series.length);
  const tickIndexes = Array.from({ length: tickCount }, (_, index) =>
    Math.round((index * (series.length - 1)) / Math.max(1, tickCount - 1)),
  );

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-[202px] w-full overflow-visible"
        role="img"
        aria-label="Response time average"
      >
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <line
            key={ratio}
            x1="0"
            x2={WIDTH}
            y1={PLOT_HEIGHT * ratio}
            y2={PLOT_HEIGHT * ratio}
            className="stroke-border-subtle"
            strokeDasharray={ratio === 1 ? undefined : "4 6"}
          />
        ))}

        {segments.map((segment, segmentIndex) => {
          const average = segment
            .map(({ point, index }) => `${x(index)},${y(point.avg_latency_ms ?? 0)}`)
            .join(" L ");
          return (
            <g key={segmentIndex}>
              <path
                d={`M ${average}`}
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinejoin="round"
                strokeLinecap="round"
                className="text-accent-text"
              />
              {segment.length === 1 && (
                <circle
                  cx={x(segment[0].index)}
                  cy={y(segment[0].point.avg_latency_ms ?? 0)}
                  r="4"
                  className="fill-accent"
                />
              )}
            </g>
          );
        })}

        {series.map((point, index) => (
          <rect
            key={point.ts}
            x={(index / series.length) * WIDTH}
            y="0"
            width={WIDTH / series.length}
            height={PLOT_HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
            onMouseLeave={() => setHover((value) => (value === index ? null : value))}
          />
        ))}

        {hover != null && series[hover]?.avg_latency_ms != null && (
          <>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1="0"
              y2={PLOT_HEIGHT}
              className="stroke-fg-muted"
              strokeDasharray="3 4"
            />
            <circle
              cx={x(hover)}
              cy={y(series[hover].avg_latency_ms)}
              r="5"
              className="fill-accent stroke-bg-panel"
              strokeWidth="3"
            />
          </>
        )}

        {tickIndexes.map((index) => (
          <text
            key={index}
            x={x(index)}
            y={HEIGHT - 2}
            textAnchor={index === 0 ? "start" : index === series.length - 1 ? "end" : "middle"}
            className="fill-fg-muted text-[10px]"
          >
            {formatTick(series[index].ts, windowValue)}
          </text>
        ))}
      </svg>

      {hover != null && series[hover] && (
        <div
          className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 rounded-md border border-border-strong bg-bg-elevated px-2.5 py-2 text-[11px] shadow-lg"
          style={{
            left: `${Math.min(92, Math.max(8, ((hover + 0.5) / series.length) * 100))}%`,
          }}
        >
          <div className="font-medium text-fg-primary">
            {formatDateTime(series[hover].ts)}
          </div>
          {series[hover].avg_latency_ms == null ? (
            <div className="mt-1 text-fg-muted">No data</div>
          ) : (
            <div className="mt-1 text-fg-secondary">
              <span>Avg <strong className="text-accent-text">{formatLatency(series[hover].avg_latency_ms)}</strong></span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
