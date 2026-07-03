/**
 * Full IANA time-zone list for time-zone <select> dropdowns.
 *
 * Uses the runtime's `Intl.supportedValuesOf("timeZone")` (Node 18+ / all
 * current browsers) and falls back to a small common set if it's unavailable.
 * Cached after first call.
 */

const FALLBACK_TIME_ZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Moscow",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

let cached: string[] | null = null;

export function ianaTimeZones(): string[] {
  if (cached) return cached;
  try {
    const fn = (Intl as unknown as {
      supportedValuesOf?: (key: string) => string[];
    }).supportedValuesOf;
    const zones = fn ? fn("timeZone") : null;
    cached = zones && zones.length > 0 ? zones : FALLBACK_TIME_ZONES;
  } catch {
    cached = FALLBACK_TIME_ZONES;
  }
  return cached;
}

/**
 * The zone list to render in a <select>, guaranteeing `current` is present
 * (so a legacy/free-form saved value still shows up as the selected option).
 */
export function timeZoneOptions(current?: string | null): string[] {
  const zones = ianaTimeZones();
  if (current && !zones.includes(current)) return [current, ...zones];
  return zones;
}

/**
 * The current UTC offset of `tz` (evaluated at `at`, since offsets shift with
 * DST) as a signed `±HH:MM` string — e.g. `"+00:00"`, `"-05:00"`, `"+05:30"`.
 * Falls back to `"+00:00"` for an unknown zone.
 */
export function tzOffsetLabel(tz: string, at: Date = new Date()): string {
  const minutes = tzOffsetMinutes(tz, at);
  if (minutes === null) return "+00:00";
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  return `${sign}${hh}:${mm}`;
}

/** Offset of `tz` from UTC in minutes at instant `at` (east positive). */
export function tzOffsetMinutes(tz: string, at: Date = new Date()): number | null {
  try {
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    const parts = dtf.formatToParts(at);
    const map: Record<string, number> = {};
    for (const p of parts) {
      if (p.type !== "literal") map[p.type] = Number(p.value);
    }
    const asUTC = Date.UTC(
      map.year,
      map.month - 1,
      map.day,
      map.hour,
      map.minute,
      map.second,
    );
    return Math.round((asUTC - at.getTime()) / 60000);
  } catch {
    return null;
  }
}

export interface TimeZoneOption {
  value: string;
  /** e.g. "America/Chicago (-05:00)". */
  label: string;
}

/**
 * Zone options for a <select>, each labelled with its current UTC offset
 * (`"UTC (+00:00)"`, `"America/Chicago (-05:00)"`). Offsets are point-in-time
 * (evaluated now) since they move with DST. Guarantees `current` is present.
 */
export function timeZoneOptionsWithOffset(
  current?: string | null,
): TimeZoneOption[] {
  const now = new Date();
  return timeZoneOptions(current).map((tz) => ({
    value: tz,
    label: `${tz} (${tzOffsetLabel(tz, now)})`,
  }));
}

/** Short zone label for a day, e.g. "EDT"; falls back to the IANA name. */
export function tzAbbrev(tz: string | null | undefined, dateIso: string): string {
  if (!tz) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "short",
    }).formatToParts(new Date(`${dateIso}T12:00:00`));
    return parts.find((p) => p.type === "timeZoneName")?.value ?? tz;
  } catch {
    return tz;
  }
}

/**
 * Convert a wall-clock `HH:MM` on calendar day `dateIso` from `fromTz` to
 * `toTz`, returning `{ time, dayShift }` where `dayShift` is -1/0/+1 if the
 * converted time lands on the previous/next day. Used to re-express a roster's
 * coverage window in a viewer-chosen display zone.
 */
export function convertWallTime(
  dateIso: string,
  hhmm: string,
  fromTz: string,
  toTz: string,
): { time: string; dayShift: number } {
  if (fromTz === toTz) return { time: hhmm, dayShift: 0 };
  const [h, m] = hhmm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return { time: hhmm, dayShift: 0 };
  // Treat the wall time as if it were UTC, then correct by fromTz's offset at
  // that instant to recover the true UTC instant.
  const guess = new Date(`${dateIso}T${hhmm}:00Z`);
  const fromOffset = tzOffsetMinutes(fromTz, guess) ?? 0;
  const instant = new Date(guess.getTime() - fromOffset * 60000);
  try {
    const dtf = new Intl.DateTimeFormat("en-CA", {
      timeZone: toTz,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    const map: Record<string, string> = {};
    for (const p of dtf.formatToParts(instant)) {
      if (p.type !== "literal") map[p.type] = p.value;
    }
    const outDate = `${map.year}-${map.month}-${map.day}`;
    const dayMs =
      new Date(`${outDate}T00:00:00Z`).getTime() -
      new Date(`${dateIso}T00:00:00Z`).getTime();
    const dayShift = Math.round(dayMs / 86_400_000);
    return { time: `${map.hour}:${map.minute}`, dayShift };
  } catch {
    return { time: hhmm, dayShift: 0 };
  }
}
