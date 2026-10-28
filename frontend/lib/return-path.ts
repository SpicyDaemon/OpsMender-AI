/**
 * Where to go after sign-in when a signed action link (M1-99) sent someone to
 * the login page. It lives in sessionStorage, so it survives the MFA and SSO
 * round trips in the same tab, and only the confirm page's own path is ever
 * accepted, so it can never send anyone to another site.
 */

const KEY = "opsmender_return_to";
export const CONFIRM_PATH = "/actions/confirm";

export function isReturnPath(path: string): boolean {
  return path === CONFIRM_PATH || path.startsWith(`${CONFIRM_PATH}#`);
}

export function rememberReturnPath(path: string): void {
  if (isReturnPath(path)) sessionStorage.setItem(KEY, path);
}

export function forgetReturnPath(): void {
  sessionStorage.removeItem(KEY);
}

/** The path to open after sign-in, used once; the dashboard otherwise. */
export function takeReturnPath(fallback = "/dashboard"): string {
  if (typeof window === "undefined") return fallback;
  const path = sessionStorage.getItem(KEY);
  sessionStorage.removeItem(KEY);
  return path !== null && isReturnPath(path) ? path : fallback;
}
