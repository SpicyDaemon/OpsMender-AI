const ORG_SLUG_KEY = "opsmender_org_slug";
const ORG_SCOPE_RE = /^\/org\/([^/]+)(\/dashboard(?:\/.*)?)$/;
const DASHBOARD_PATH_RE = /^\/dashboard(?:\/|$)/;
const ORG_DASHBOARD_PATH_RE = /^\/(?:org|o)\/[^/]+\/dashboard(?:\/|$)/;

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
  try {
    window.dispatchEvent(new CustomEvent("opsmender:org-slug-updated"));
  } catch {
    // Event dispatch can fail in unusual embedded test/browser contexts.
  }
}

export function stripOrgScope(pathname: string): string {
  return pathname.match(ORG_SCOPE_RE)?.[2] ?? pathname;
}

export function scopeDashboardPath(pathname: string, slug = getOrgSlug()): string {
  if (!slug || !DASHBOARD_PATH_RE.test(pathname)) return pathname;
  return `/org/${encodeURIComponent(slug)}${pathname}`;
}

export function scopeDashboardHref(href: string, slug = getOrgSlug()): string {
  if (!slug) return href;
  if (href.startsWith("/org/") || href.startsWith("/o/")) return href;

  if (href.startsWith("/")) {
    const match = href.match(/^([^?#]*)(.*)$/);
    const path = match?.[1] ?? href;
    const suffix = match?.[2] ?? "";
    return `${scopeDashboardPath(path, slug)}${suffix}`;
  }

  if (typeof window === "undefined") return href;
  try {
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin) return href;
    const scopedPath = scopeDashboardPath(url.pathname, slug);
    if (scopedPath === url.pathname) return href;
    return `${scopedPath}${url.search}${url.hash}`;
  } catch {
    return href;
  }
}

export function isOrgScopedDashboardHref(href: string): boolean {
  if (ORG_DASHBOARD_PATH_RE.test(href)) return true;
  if (typeof window === "undefined") return false;
  try {
    const url = new URL(href, window.location.origin);
    return url.origin === window.location.origin && ORG_DASHBOARD_PATH_RE.test(url.pathname);
  } catch {
    return false;
  }
}

export function isDashboardHref(href: string): boolean {
  if (DASHBOARD_PATH_RE.test(href) || ORG_DASHBOARD_PATH_RE.test(href)) return true;
  if (typeof window === "undefined") return false;
  try {
    const url = new URL(href, window.location.origin);
    return (
      url.origin === window.location.origin &&
      (DASHBOARD_PATH_RE.test(url.pathname) || ORG_DASHBOARD_PATH_RE.test(url.pathname))
    );
  } catch {
    return false;
  }
}
