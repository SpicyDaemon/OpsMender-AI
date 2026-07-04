/**
 * One date/time formatting system for the whole dashboard.
 *
 * Before this existed, ~60 call sites used raw `toLocale*` with three
 * different visible formats. Everything user-facing should route through
 * these helpers so dates read the same everywhere:
 *
 *   formatDate      → "Jun 24, 2026"
 *   formatDateTime  → "Jun 24, 2026, 11:02 PM"   (no seconds)
 *   formatTime      → "11:02 PM"                  (no seconds)
 *   formatRelative  → "just now" / "9 minutes ago" for < 7 days,
 *                     otherwise the absolute formatDate.
 *
 * All accept a Date, an ISO string, or an epoch-ms number, and return an
 * empty string for null/undefined/invalid input (never "Invalid Date").
 */

type DateInput = Date | string | number | null | undefined;

function toDate(input: DateInput): Date | null {
  if (input == null) return null;
  const d = input instanceof Date ? input : new Date(input);
  return Number.isNaN(d.getTime()) ? null : d;
}

const DATE_OPTS: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  year: "numeric",
};

const TIME_OPTS: Intl.DateTimeFormatOptions = {
  hour: "numeric",
  minute: "2-digit",
};

export function formatDate(input: DateInput): string {
  const d = toDate(input);
  return d ? d.toLocaleDateString(undefined, DATE_OPTS) : "";
}

export function formatDateTime(input: DateInput): string {
  const d = toDate(input);
  return d ? d.toLocaleString(undefined, { ...DATE_OPTS, ...TIME_OPTS }) : "";
}

export function formatTime(input: DateInput): string {
  const d = toDate(input);
  return d ? d.toLocaleTimeString(undefined, TIME_OPTS) : "";
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Relative time for recent instants, absolute date past 7 days.
 * Handles both past ("9 minutes ago") and future ("in 9 minutes").
 */
export function formatRelative(input: DateInput, now: DateInput = Date.now()): string {
  const d = toDate(input);
  if (!d) return "";
  const nowMs = toDate(now)?.getTime() ?? Date.now();
  const diff = nowMs - d.getTime();
  const past = diff >= 0;
  const abs = Math.abs(diff);

  if (abs < 45 * 1000) return "just now";

  let value: number;
  let unit: Intl.RelativeTimeFormatUnit;
  if (abs < HOUR) {
    value = Math.round(abs / MINUTE);
    unit = "minute";
  } else if (abs < DAY) {
    value = Math.round(abs / HOUR);
    unit = "hour";
  } else if (abs < 7 * DAY) {
    value = Math.round(abs / DAY);
    unit = "day";
  } else {
    // Beyond a week, an absolute date is clearer than "37 days ago".
    return formatDate(d);
  }

  const plural = value === 1 ? "" : "s";
  return past ? `${value} ${unit}${plural} ago` : `in ${value} ${unit}${plural}`;
}
