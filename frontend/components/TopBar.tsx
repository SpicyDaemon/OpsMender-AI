"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  Bell,
  Building2,
  Check,
  ChevronDown,
  Keyboard,
  LogOut,
  Menu,
} from "lucide-react";
import { useAuth } from "@/context/auth";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import {
  getConfig,
  getOrgId,
  listApprovals,
  listMyOrganizations,
  resolveTenant,
  setMyPrimaryOrganization,
  setOrgId,
} from "@/lib/api";
import type { MyOrganizationResponse, TenantContextResponse } from "@/lib/types";

const ROLE_STYLES: Record<string, string> = {
  admin: "bg-status-info-bg text-status-info border-status-info-border",
  operator: "bg-status-low-bg text-status-low border-status-low-border",
  viewer: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
};

const POLL_MS = 30_000;

export function TopBar({
  onOpenMobileNav,
}: {
  onOpenMobileNav?: () => void;
}) {
  const { user, logout } = useAuth();
  const [pending, setPending] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [orgs, setOrgs] = useState<MyOrganizationResponse[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [orgMenuOpen, setOrgMenuOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const orgMenuRef = useRef<HTMLDivElement | null>(null);
  const [tenant, setTenant] = useState<TenantContextResponse | null>(null);
  // Sprint 64 Step 2: gate the org switcher on multi_org_enabled.
  // Default false = single-workspace mode (don't show the switcher).
  const [multiOrgEnabled, setMultiOrgEnabled] = useState(false);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    async function load() {
      try {
        const res = await listApprovals({ status: "pending", limit: 100 });
        if (!cancelled) setPending(res.items.length);
      } catch {
        if (!cancelled) setPending(null);
      }
    }
    load();
    const t = setInterval(load, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [user]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
      if (orgMenuRef.current && !orgMenuRef.current.contains(e.target as Node)) {
        setOrgMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  useEffect(() => {
    let cancelled = false;
    resolveTenant()
      .then((t) => { if (!cancelled) setTenant(t); })
      .catch(() => { if (!cancelled) setTenant(null); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    getConfig()
      .then((c) => { if (!cancelled) setMultiOrgEnabled(c.multi_org_enabled ?? false); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    listMyOrganizations()
      .then((res) => {
        if (cancelled) return;
        setOrgs(res.items);
        const stored = getOrgId();
        const primary = res.items.find((o) => o.is_primary);
        const current =
          (stored && res.items.find((o) => o.id === stored)?.id) ||
          primary?.id ||
          res.items[0]?.id ||
          null;
        setActiveOrgId(current);
        if (current && current !== stored) setOrgId(current);
      })
      .catch(() => {
        if (!cancelled) setOrgs([]);
      });
    return () => { cancelled = true; };
  }, [user]);

  async function handleSwitchOrg(org: MyOrganizationResponse) {
    if (org.id === activeOrgId || switching) return;
    setSwitching(true);
    try {
      setOrgId(org.id);
      await setMyPrimaryOrganization(org.id);
      setActiveOrgId(org.id);
      setOrgMenuOpen(false);
      // Reload so every page-level fetch reruns under the new org context.
      window.location.reload();
    } catch {
      setSwitching(false);
    }
  }

  const activeOrg = orgs.find((o) => o.id === activeOrgId) ?? null;
  // Per-org role for the *currently displayed* tenant. When the host pins a
  // tenant, we look up the user's role in that org from the listMyOrganizations
  // response — `tenant.org_id` is set, so we match by that.
  const activeOrgRole = tenant?.pinned
    ? orgs.find((o) => o.id === tenant.org_id)?.role ?? null
    : activeOrg?.role ?? null;
  const orgRoleClass = activeOrgRole
    ? ROLE_STYLES[activeOrgRole] ?? ROLE_STYLES.viewer
    : "";

  const roleClass = user ? ROLE_STYLES[user.role] ?? ROLE_STYLES.viewer : "";
  const pendingLabel = pending === null ? "?" : pending > 99 ? "99+" : String(pending);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-bg-elevated px-4">
      <div className="flex items-center">
        <button
          type="button"
          onClick={onOpenMobileNav}
          className="flex h-9 w-9 items-center justify-center rounded-md text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors sm:hidden"
          title="Open navigation"
          aria-label="Open navigation"
        >
          <Menu size={18} />
        </button>
      </div>
      <div className="flex min-w-0 items-center gap-1.5">
        {user && tenant?.pinned && (
          <div
            title={
              activeOrgRole
                ? `Active organization: ${tenant.org_name} — your role: ${activeOrgRole} (pinned by host ${tenant.host})`
                : `Active organization: ${tenant.org_name} (pinned by host ${tenant.host})`
            }
            aria-label={`Active organization: ${tenant.org_name}, pinned by host${activeOrgRole ? `, role ${activeOrgRole}` : ""}`}
            className="flex h-9 items-center gap-2 rounded-md border border-border-subtle bg-bg-input px-2.5 text-sm text-fg-secondary"
          >
            <Building2 size={14} className="shrink-0 text-fg-muted" />
            <span className="max-w-[140px] sm:max-w-[180px] truncate font-medium text-fg-primary">
              {tenant.org_name}
            </span>
            {activeOrgRole && (
              <span
                className={`rounded-pill border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide ${orgRoleClass}`}
              >
                {activeOrgRole}
              </span>
            )}
            <span className="hidden lg:inline rounded-pill border border-border-subtle bg-bg-panel px-1.5 py-px font-mono text-[10px] uppercase tracking-wide text-fg-muted">
              host-pinned
            </span>
          </div>
        )}
        {/* Sprint 64 Step 2: org switcher only renders in multi-workspace
            mode. In single-workspace mode (the default) the active org
            is implicit and the switcher would be noise. Host-pinned
            tenants always show the read-only badge above regardless. */}
        {user && !tenant?.pinned && orgs.length > 0 && multiOrgEnabled && (
          <div ref={orgMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setOrgMenuOpen((o) => !o)}
              title={
                activeOrg
                  ? activeOrgRole
                    ? `Active organization: ${activeOrg.name} — your role: ${activeOrgRole} (click to switch)`
                    : `Active organization: ${activeOrg.name} — click to switch`
                  : "Switch organization"
              }
              aria-label={
                activeOrg
                  ? `Active organization: ${activeOrg.name}${activeOrgRole ? `, role ${activeOrgRole}` : ""}`
                  : "Select organization"
              }
              className="flex h-9 items-center gap-2 rounded-md border border-border-subtle bg-bg-input px-2.5 text-sm text-fg-secondary hover:border-border-strong hover:bg-bg-hover hover:text-fg-primary transition-colors"
            >
              <Building2 size={14} className="shrink-0 text-fg-muted" />
              <span className="max-w-[120px] sm:max-w-[160px] truncate font-medium text-fg-primary">
                {activeOrg?.name ?? "Select org"}
              </span>
              {activeOrgRole && (
                <span
                  className={`rounded-pill border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide ${orgRoleClass}`}
                >
                  {activeOrgRole}
                </span>
              )}
              <ChevronDown size={14} className="text-fg-muted" />
            </button>
            {orgMenuOpen && (
              <div className="absolute right-0 top-full z-20 mt-2 w-64 overflow-hidden rounded-md border border-border-subtle bg-bg-panel shadow-lg">
                <div className="border-b border-border-subtle px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                  Organizations
                </div>
                <ul className="max-h-72 overflow-y-auto py-1">
                  {orgs.map((org) => {
                    const isActive = org.id === activeOrgId;
                    return (
                      <li key={org.id}>
                        <button
                          onClick={() => handleSwitchOrg(org)}
                          disabled={switching}
                          className="flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-bg-hover transition-colors disabled:opacity-50"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-medium text-fg-primary">{org.name}</p>
                            <p className="truncate font-mono text-[11px] text-fg-muted">
                              {org.slug} · {org.role}
                            </p>
                          </div>
                          {isActive && <Check size={14} className="mt-1 shrink-0 text-status-low" />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </div>
        )}
        <button
          type="button"
          onClick={() => window.dispatchEvent(new CustomEvent("opsmender:open-shortcuts"))}
          title="Keyboard shortcuts (?)"
          className="hidden sm:flex h-9 w-9 items-center justify-center rounded-md text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
        >
          <Keyboard size={16} />
        </button>

        <Link
          href="/dashboard/approvals"
          title={`${pending ?? 0} pending approvals`}
          className="relative flex h-9 w-9 items-center justify-center rounded-md text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
        >
          <Bell size={16} />
          {pending !== null && pending > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex min-w-[18px] items-center justify-center rounded-pill border border-bg-elevated bg-status-critical px-1 font-mono text-[10px] font-semibold leading-4 text-fg-primary">
              {pendingLabel}
            </span>
          )}
        </Link>

        {user && (
          <div ref={menuRef} className="relative">
            <button
              onClick={() => setMenuOpen((o) => !o)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-bg-panel text-xs font-semibold uppercase">
                {user.username.slice(0, 2)}
              </span>
              <span className="hidden md:inline font-medium text-fg-primary">{user.username}</span>
              <ChevronDown size={14} className="text-fg-muted" />
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full mt-2 w-56 overflow-hidden rounded-md border border-border-subtle bg-bg-panel shadow-lg">
                <div className="border-b border-border-subtle px-3 py-2.5">
                  <p className="truncate text-sm font-medium text-fg-primary">{user.username}</p>
                  <span
                    className={`mt-1 inline-block rounded-pill border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${roleClass}`}
                  >
                    {user.role}
                  </span>
                  {(tenant?.pinned ? tenant.org_name : activeOrg?.name) && (
                    <div className="mt-2 flex items-start gap-1.5 text-xs text-fg-secondary">
                      <Building2 size={12} className="mt-0.5 shrink-0 text-fg-muted" />
                      <div className="min-w-0 flex-1">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                          Active org{tenant?.pinned ? " · host-pinned" : ""}
                        </p>
                        <div className="flex items-center gap-1.5">
                          <p className="truncate font-medium text-fg-primary">
                            {tenant?.pinned ? tenant.org_name : activeOrg?.name}
                          </p>
                          {activeOrgRole && (
                            <span
                              className={`rounded-pill border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide ${orgRoleClass}`}
                            >
                              {activeOrgRole}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                <div className="border-b border-border-subtle px-3 py-2.5">
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                    Theme
                  </p>
                  <ThemeToggle />
                </div>
                <button
                  onClick={() => { setMenuOpen(false); logout(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-sm text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
                >
                  <LogOut size={14} />
                  Sign out
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
