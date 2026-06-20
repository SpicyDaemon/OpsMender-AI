const ORG_SLUG_KEY = "opsmender_org_slug";
const ORG_SCOPE_RE = /^\/o\/([^/]+)(\/dashboard(?:\/.*)?)$/;

export function getOrgSlug(): string | null {
  if (typeof window === "undefined") return null;
  const scoped = window.location.pathname.match(ORG_SCOPE_RE);
  if (scoped?.[1]) return decodeURIComponent(scoped[1]);
  try {
    return typeof window.localStorage?.getItem === "function"
      ? window.localStorage.getItem(ORG_SLUG_KEY)
      : null;
  } catch {
    return null;
  }
}

export function setOrgSlug(slug: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (slug && typeof window.localStorage?.setItem === "function") {
      window.localStorage.setItem(ORG_SLUG_KEY, slug);
    } else if (!slug && typeof window.localStorage?.removeItem === "function") {
      window.localStorage.removeItem(ORG_SLUG_KEY);
    }
  } catch {
    // Storage can be unavailable in private browsing or embedded contexts.
  }
}

export function stripOrgScope(pathname: string): string {
  return pathname.match(ORG_SCOPE_RE)?.[2] ?? pathname;
}

export function scopeDashboardPath(pathname: string, slug = getOrgSlug()): string {
  if (!slug || !pathname.startsWith("/dashboard")) return pathname;
  return `/o/${encodeURIComponent(slug)}${pathname}`;
}
