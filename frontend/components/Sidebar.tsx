"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Radar,
  FileText,
  LogOut,
  Settings,
} from "lucide-react";
import { useAuth } from "@/context/auth";
import { getConfig } from "@/lib/api";

const NAV = [
  { href: "/dashboard/incidents", label: "Incidents", icon: AlertTriangle },
  { href: "/dashboard/approvals", label: "Approvals", icon: CheckSquare },
  { href: "/dashboard/detectors", label: "Detectors", icon: Radar },
  { href: "/dashboard/skills", label: "Skills", icon: FileText },
  { href: "/dashboard/audit", label: "Audit Log", icon: BookOpen },
  { href: "/dashboard/reliability", label: "Reliability", icon: Activity },
  { href: "/dashboard/organizations", label: "Organizations", icon: CheckSquare, reqRole: "admin" },
  { href: "/dashboard/config", label: "Config", icon: Settings },
];

const COLLAPSE_KEY = "aim:sidebar-collapsed";

const ROLE_STYLES: Record<string, string> = {
  admin: "bg-status-info-bg text-status-info border-status-info-border",
  operator: "bg-status-low-bg text-status-low border-status-low-border",
  viewer: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
};

const TIER_STYLES: Record<number, { label: string; cls: string }> = {
  0: { label: "Autonomous", cls: "bg-status-critical-bg text-status-critical border-status-critical-border" },
  1: { label: "Approval", cls: "bg-status-high-bg text-status-high border-status-high-border" },
  2: { label: "Assisted", cls: "bg-status-medium-bg text-status-medium border-status-medium-border" },
  3: { label: "Advisory", cls: "bg-status-low-bg text-status-low border-status-low-border" },
};

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [tier, setTier] = useState<number | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem(COLLAPSE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === "1") setCollapsed(true);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    getConfig()
      .then((c) => { if (!cancelled) setTier(c.tier); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [user, pathname]);

  useEffect(() => {
    if (hydrated) localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed, hydrated]);

  const width = collapsed ? "w-16" : "w-60";
  const roleClass = user ? ROLE_STYLES[user.role] ?? ROLE_STYLES.viewer : "";
  const tierInfo = tier !== null ? TIER_STYLES[tier] : null;

  const visibleNav = NAV.filter(n => !n.reqRole || (user && user.role === n.reqRole));

  return (
    <aside
      className={`${width} flex h-full shrink-0 flex-col border-r border-border-subtle bg-bg-elevated transition-[width] duration-200`}
    >
      {/* Brand */}
      <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-4 h-16">
        <Image
          src="/logo.png"
          alt="AIM"
          width={32}
          height={32}
          className="shrink-0 rounded-md"
        />
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <p className="text-sm font-semibold text-fg-primary tracking-tight">AIM</p>
            <p className="text-[11px] text-fg-muted truncate">AI Incident Manager</p>
          </div>
        )}
      </div>

      {/* Tier indicator */}
      {tierInfo && (
        <div className="border-b border-border-subtle px-3 py-2">
          {collapsed ? (
            <div
              title={`Tier ${tier} · ${tierInfo.label}`}
              className={`mx-auto flex h-6 w-6 items-center justify-center rounded-md border font-mono text-[11px] font-semibold ${tierInfo.cls}`}
            >
              T{tier}
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wide text-fg-muted">
                Tier
              </span>
              <span
                className={`inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tierInfo.cls}`}
              >
                <span className="font-mono">T{tier}</span>
                <span>{tierInfo.label}</span>
              </span>
            </div>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-2 py-3 overflow-y-auto">
        {visibleNav.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={`group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors ${
                active
                  ? "bg-accent-bg text-accent"
                  : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
              }`}
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && <span className="truncate">{label}</span>}
              {active && !collapsed && (
                <span className="ml-auto h-1.5 w-1.5 rounded-pill bg-accent" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="mx-2 mb-2 flex items-center justify-center gap-2 rounded-md border border-border-subtle py-1.5 text-xs text-fg-muted hover:bg-bg-hover hover:text-fg-secondary transition-colors"
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <ChevronRight size={14} /> : (
          <>
            <ChevronLeft size={14} />
            <span>Collapse</span>
          </>
        )}
      </button>

      {/* User */}
      <div className="border-t border-border-subtle px-3 py-3">
        {user && (
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-bg-panel text-xs font-semibold text-fg-secondary uppercase">
              {user.username.slice(0, 2)}
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-fg-primary">
                    {user.username}
                  </p>
                  <span
                    className={`mt-0.5 inline-block rounded-pill border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${roleClass}`}
                  >
                    {user.role}
                  </span>
                </div>
                <button
                  onClick={logout}
                  title="Sign out"
                  className="rounded-md p-1.5 text-fg-muted hover:bg-bg-hover hover:text-fg-primary transition-colors"
                >
                  <LogOut size={15} />
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
