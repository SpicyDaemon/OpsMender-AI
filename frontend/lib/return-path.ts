/**
 * One-time destination after authentication in the same tab. Only signed
 * confirmation and identified incident/session detail paths are accepted.
 * MFA, SSO and required password changes reuse this same return contract.
 */

const KEY = "opsmender_return_to";
export const CONFIRM_PATH = "/actions/confirm";
let signingOut = false;

export function isReturnPath(path: string): boolean {
  if (path === CONFIRM_PATH || path.startsWith(`${CONFIRM_PATH}#`)) return true;
  if (path.length > 256) return false;
  const detail = /^\/dashboard\/(?:incidents|sessions)\/detail\?([^#]+)$/.exec(path);
  if (!detail) return false;
  const fields = detail[1].split("&");
  if (fields.length > 2 || fields.some((field) => !/^(?:id|from)=[^&]+$/.test(field))) return false;
  const params = new URLSearchParams(detail[1]);
  const ids = params.getAll("id");
  const sources = params.getAll("from");
  return ids.length === 1 &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ids[0]) &&
    sources.length <= 1 &&
    (sources.length === 0 || /^[a-z][a-z0-9_-]{0,31}$/.test(sources[0]));
}

export function rememberReturnPath(path: string): void {
  if (typeof window === "undefined" || signingOut || !isReturnPath(path)) return;
  try {
    sessionStorage.setItem(KEY, path);
  } catch {
    // Unavailable storage must not prevent the authentication redirect.
  }
}

export function rememberCurrentReturnPath(): void {
  if (typeof window === "undefined") return;
  rememberReturnPath(`${window.location.pathname}${window.location.search ?? ""}${window.location.hash ?? ""}`);
}

export function forgetReturnPath(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Storage can be disabled by the browser.
  }
}

/** Full-page sign-out resets this module; ignore pending guard/API captures. */
export function forgetReturnPathForSignOut(): void {
  signingOut = true;
  forgetReturnPath();
}

/** The path to open after sign-in, used once; the dashboard otherwise. */
export function takeReturnPath(fallback = "/dashboard"): string {
  if (typeof window === "undefined" || signingOut) return fallback;
  try {
    const path = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return path !== null && isReturnPath(path) ? path : fallback;
  } catch {
    return fallback;
  }
}
