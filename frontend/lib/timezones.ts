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
