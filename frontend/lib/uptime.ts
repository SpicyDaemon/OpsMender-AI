/**
 * Uptime / SLA formatting helpers (Reliability v1).
 *
 * Percentages support up to 3 decimal places with adaptive precision, so
 * 99.999% renders as "99.999%" and is never rounded up to "100%".
 */

import type { UptimeStatus } from "./types";

/**
 * Format an uptime/SLA percentage (0–100) with adaptive precision:
 *   100      -> "100%"
 *   99.9     -> "99.9%"
 *   99.99    -> "99.99%"
 *   99.999   -> "99.999%"
 * Trailing zeros are trimmed; values are truncated (not rounded up) at 3
 * decimals so 99.999 never becomes 100.
 */
export function formatUptimePct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  // Truncate to 3 decimals so 99.9994 -> 99.999 (never rounds up to 100).
  const truncated = Math.floor(value * 1000) / 1000;
  if (truncated >= 100) return "100%";
  // Trim trailing zeros while keeping at least one decimal for sub-100 values.
  let s = truncated.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (!s.includes(".")) s = `${s}.0`;
  return `${s}%`;
}

/** Mean Time Between Failures: seconds -> human string ("12.3 days"). */
export function formatMtbf(seconds: number | null | undefined): string {
  if (seconds == null) return "No downtime";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(1)} min`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours.toFixed(1)} hours`;
  const days = hours / 24;
  return `${days.toFixed(1)} days`;
}

/** Downtime/duration: seconds -> compact human string. */
export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "0 min";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  if (hours < 24) return rem ? `${hours}h ${rem}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remH = hours % 24;
  return remH ? `${days}d ${remH}h` : `${days}d`;
}

export const STATUS_LABEL: Record<UptimeStatus, string> = {
  up: "Up",
  down: "Down",
  unknown: "Unknown",
};

/** Tailwind class fragments for each status, used for dots/badges. */
export function statusColors(status: UptimeStatus): {
  dot: string;
  text: string;
  bg: string;
} {
  switch (status) {
    case "up":
      return { dot: "bg-status-success", text: "text-status-success", bg: "bg-status-success-bg" };
    case "down":
      return { dot: "bg-status-critical", text: "text-status-critical", bg: "bg-status-critical-bg" };
    default:
      return { dot: "bg-fg-muted", text: "text-fg-muted", bg: "bg-bg-elevated" };
  }
}

export const WINDOW_OPTIONS: { value: string; label: string }[] = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "365d", label: "Last 365 days" },
];

/** Window seconds presets for SLOs (kept simple for v1). */
export const SLO_WINDOW_OPTIONS: { value: number; label: string }[] = [
  { value: 7 * 86400, label: "7 days" },
  { value: 30 * 86400, label: "30 days" },
  { value: 90 * 86400, label: "90 days" },
  { value: 365 * 86400, label: "365 days" },
];
