/**
 * Build a service's full, copyable alert intake URL.
 *
 * The backend returns `intake_url` as a relative path
 * (`/api/v1/intake/{token}`). The absolute origin comes from the configured
 * `OPSMENDER_PUBLIC_BASE_URL` (surfaced via `/config` as `public_base_url`)
 * and falls back to `window.location.origin` when unset, per the v1 spec.
 */
export function fullIntakeUrl(
  intakeUrl: string | null | undefined,
  publicBaseUrl?: string | null,
): string | null {
  if (!intakeUrl) return null;
  // Already absolute — return as-is.
  if (/^https?:\/\//i.test(intakeUrl)) return intakeUrl;
  const configured = publicBaseUrl?.trim();
  const origin = configured
    ? configured.replace(/\/+$/, "")
    : typeof window !== "undefined"
      ? window.location.origin
      : "";
  const path = intakeUrl.startsWith("/") ? intakeUrl : `/${intakeUrl}`;
  return `${origin}${path}`;
}
