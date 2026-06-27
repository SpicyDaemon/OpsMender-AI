"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  Building2,
  ChevronDown,
  Keyboard,
  LogOut,
  Menu,
  UserCircle,
} from "lucide-react";
import { useAuth } from "@/context/auth";
import { Avatar, userDisplayName } from "@/components/ui/Avatar";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { NotificationBell } from "@/components/NotificationBell";
import { resolveTenant } from "@/lib/api";
import type { TenantContextResponse } from "@/lib/types";

const ROLE_STYLES: Record<string, string> = {
  admin: "bg-status-info-bg text-status-info border-status-info-border",
  operator: "bg-status-low-bg text-status-low border-status-low-border",
  viewer: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
};

export function TopBar({
  onOpenMobileNav,
}: {
  onOpenMobileNav?: () => void;
}) {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [tenant, setTenant] = useState<TenantContextResponse | null>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
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

  const orgName = tenant?.org_name ?? null;
  const orgRole = user?.role ?? null;
  const orgRoleClass = orgRole ? ROLE_STYLES[orgRole] ?? ROLE_STYLES.viewer : "";
  const roleClass = user ? ROLE_STYLES[user.role] ?? ROLE_STYLES.viewer : "";

  return (
    <header className="relative z-40 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-bg-elevated px-4">
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
        {user && orgName && (
          <div
            title={
              orgRole
                ? `Organization: ${orgName} — your role: ${orgRole}`
                : `Organization: ${orgName}`
            }
            aria-label={`Organization: ${orgName}${orgRole ? `, role ${orgRole}` : ""}`}
            className="hidden h-9 items-center gap-2 rounded-md border border-border-subtle bg-bg-input px-2.5 text-sm text-fg-secondary sm:flex"
          >
            <Building2 size={14} className="shrink-0 text-fg-muted" />
            <span className="max-w-[140px] truncate font-medium text-fg-primary lg:max-w-[180px]">
              {orgName}
            </span>
            {orgRole && (
              <span
                className={`rounded-pill border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide ${orgRoleClass}`}
              >
                {orgRole}
              </span>
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

        <NotificationBell />

        {user && (
          <div ref={menuRef} className="relative">
            <button
              onClick={() => setMenuOpen((o) => !o)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
            >
              <Avatar user={user} size={28} />
              <span className="hidden md:inline font-medium text-fg-primary">
                {userDisplayName(user)}
              </span>
              <ChevronDown size={14} className="text-fg-muted" />
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-md border border-border-subtle bg-bg-panel shadow-xl">
                <div className="border-b border-border-subtle px-3 py-2.5">
                  <div className="flex items-center gap-2.5">
                    <Avatar user={user} size={32} />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-fg-primary">
                        {userDisplayName(user)}
                      </p>
                      <p className="truncate text-xs text-fg-muted">{user.email}</p>
                    </div>
                  </div>
                  <span
                    className={`mt-2 inline-block rounded-pill border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${roleClass}`}
                  >
                    {user.role}
                  </span>
                  {orgName && (
                    <div className="mt-2 flex items-start gap-1.5 text-xs text-fg-secondary">
                      <Building2 size={12} className="mt-0.5 shrink-0 text-fg-muted" />
                      <div className="min-w-0 flex-1">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                          Organization
                        </p>
                        <p className="truncate font-medium text-fg-primary">
                          {orgName}
                        </p>
                      </div>
                    </div>
                  )}
                </div>
                <Link
                  href="/dashboard/settings/profile"
                  onClick={() => setMenuOpen(false)}
                  className="flex w-full items-center gap-2 border-b border-border-subtle px-3 py-2.5 text-sm text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
                >
                  <UserCircle size={14} />
                  Edit Profile
                </Link>
                <div className="border-b border-border-subtle px-3 py-2.5">
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                    Theme
                  </p>
                  <ThemeToggle full />
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
