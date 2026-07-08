"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  CalendarClock,
  LayoutDashboard,
  BookOpen,
  Brain,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Gauge,
  FileText,
  FileBarChart,
  LogOut,
  Network,
  Plug,
  Settings,
  UserCog,
  X,
} from "lucide-react";
import { useAuth } from "@/context/auth";
import { usePendingApprovalsCount } from "@/context/liveEvents";
import { useTheme } from "@/context/theme";
import { getConfig } from "@/lib/api";
import { autonomyTierDisplay } from "@/lib/displayNames";

type NavItem = {
  href: string;
  label: string;
  icon: typeof AlertTriangle;
  reqRole?: string;
  /** Roles allowed to see this item. Omit = visible to everyone. */
  roles?: string[];
  badge?: { label: string; tone: "neutral" | "warn" };
  /**
   * When true, the active-state match requires `pathname === href`
   * (not `startsWith`). Use this for href values that are prefixes
   * of other routes — e.g. `/dashboard` would otherwise light up on
   * every child page.
   */
  exact?: boolean;
};

type NavGroup = {
  id: string;
  label: string;
  items: NavItem[];
};

/** Whether a nav item is visible to a given role. Omitting both gates =
 *  visible to everyone. Exported for tests + the render filter. */
export function navItemVisibleForRole(
  item: NavItem,
  role: string | undefined | null,
): boolean {
  if (item.reqRole) return Boolean(role && role === item.reqRole);
  if (item.roles) return Boolean(role && item.roles.includes(role));
  return true;
}

/**
 * Roles allowed to access a dashboard path, derived from the nav model so the
 * route guard and the sidebar stay in sync. Returns ``null`` when the path is
 * unrestricted (no matching gated nav item — e.g. self-service settings).
 * Uses the most specific (longest) matching nav href.
 */
export function requiredRolesForPath(pathname: string): string[] | null {
  const items = buildNavGroups().flatMap((g) => g.items);
  let best: NavItem | null = null;
  for (const item of items) {
    if (pathname === item.href || pathname.startsWith(item.href + "/")) {
      if (!best || item.href.length > best.href.length) best = item;
    }
  }
  if (!best) return null;
  if (best.reqRole) return [best.reqRole];
  if (best.roles) return best.roles;
  return null;
}

export function buildNavGroups(): NavGroup[] {
  return [
    {
      id: "incident-management",
      label: "Incident Management",
      items: [
        // Dashboard + Incidents are visible to everyone (Viewer = read-only).
        { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, exact: true },
        { href: "/dashboard/incidents", label: "Incidents", icon: AlertTriangle },
        { href: "/dashboard/approvals", label: "Approvals", icon: CheckSquare, roles: ["admin", "operator"] },
      ],
    },
    {
      // Paging configuration is workspace setup — admin only.
      id: "paging",
      label: "Paging & On-call",
      items: [
        // On Call Schedule is visible to everyone (Viewer = read-only);
        // editing the schedule is gated in the page itself.
        { href: "/dashboard/on-call-schedule", label: "On Call Schedule", icon: CalendarClock },
        // The hub owns Teams/Rosters/Chains/Services/Windows/Notifications
        // as in-page tabs; operators can view it in read-only mode.
        { href: "/dashboard/paging", label: "Paging", icon: Bell, roles: ["admin", "operator"] },
      ],
    },
    {
      // AI configuration surfaces are admin only.
      id: "ai-agent",
      label: "AI Agent",
      items: [
        { href: "/dashboard/models", label: "Models", icon: Cpu, roles: ["admin"] },
        { href: "/dashboard/mcp-servers", label: "MCP Servers", icon: Network, roles: ["admin"] },
        { href: "/dashboard/skills", label: "MCP Skills", icon: FileText, roles: ["admin"] },
        { href: "/dashboard/memories", label: "Memories", icon: Brain, roles: ["admin"] },
        { href: "/dashboard/orchestration", label: "Orchestration", icon: Gauge, roles: ["admin", "operator"] },
      ],
    },
    {
      id: "observe",
      label: "Observe",
      items: [
        { href: "/dashboard/reliability", label: "Reliability", icon: Activity, roles: ["admin", "operator"] },
        { href: "/dashboard/analytics", label: "Analytics", icon: BarChart3, roles: ["admin", "operator"] },
        { href: "/dashboard/reports", label: "Reports", icon: FileBarChart, roles: ["admin", "operator"] },
        { href: "/dashboard/activity", label: "Activity", icon: BookOpen, roles: ["admin", "operator"] },
      ],
    },
    {
      id: "admin",
      label: "Admin",
      items: [
        { href: "/dashboard/people", label: "People", icon: UserCog, roles: ["admin"] },
        { href: "/dashboard/integrations", label: "Integrations", icon: Plug, roles: ["admin"] },
        { href: "/dashboard/config", label: "Settings", icon: Settings, roles: ["admin"] },
      ],
    },
  ];
}

export function applyApprovalsBadge(
  groups: NavGroup[],
  pendingCount: number,
): NavGroup[] {
  return groups.map((group) => ({
    ...group,
    items: group.items.map((item) => {
      if (item.href !== "/dashboard/approvals") return item;
      if (pendingCount <= 0) {
        return { ...item, badge: undefined };
      }
      return {
        ...item,
        badge: {
          label: pendingCount > 9 ? "9+" : String(pendingCount),
          tone: "warn" as const,
        },
      };
    }),
  }));
}

const COLLAPSE_KEY = "opsmender:sidebar-collapsed";
const LEGACY_GROUP_COLLAPSE_KEY = "opsmender:sidebar-groups-collapsed";

const ROLE_STYLES: Record<string, string> = {
  admin: "bg-status-info-bg text-status-info border-status-info-border",
  operator: "bg-status-low-bg text-status-low border-status-low-border",
  viewer: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
};

type NavLinkArgs = {
  href: string;
  label: string;
  Icon: typeof AlertTriangle;
  badge?: NavItem["badge"];
  active: boolean;
  collapsed: boolean;
  onClick?: () => void;
};

function renderNavLink({
  href,
  label,
  Icon,
  badge,
  active,
  collapsed,
  onClick,
}: NavLinkArgs) {
  return (
    <Link
      key={href}
      href={href}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors ${
        active
          ? "bg-accent-bg text-accent-text"
          : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
      }`}
    >
      <Icon size={16} className="shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
      {!collapsed && badge && (
        <span
          className={`ml-auto rounded-pill border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${
            badge.tone === "warn"
              ? "border-status-high-border bg-status-high-bg text-status-high"
              : "border-status-neutral-border bg-status-neutral-bg text-fg-secondary"
          }`}
        >
          {badge.label}
        </span>
      )}
      {active && !collapsed && !badge && (
        <span className="ml-auto h-1.5 w-1.5 rounded-pill bg-accent-text" />
      )}
    </Link>
  );
}

type SidebarProps = {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
};

type SidebarContentProps = {
  collapsed: boolean;
  pathname: string;
  roleClass: string;
  tier: number | null;
  visibleGroups: NavGroup[];
  flatVisibleItems: NavItem[];
  user: ReturnType<typeof useAuth>["user"];
  logout: () => void;
  onToggleCollapse?: () => void;
  onNavigate?: () => void;
  showCollapseToggle?: boolean;
  mobile?: boolean;
  onMobileClose?: () => void;
};

function SidebarContent({
  collapsed,
  pathname,
  roleClass,
  tier,
  visibleGroups,
  flatVisibleItems,
  user,
  logout,
  onToggleCollapse,
  onNavigate,
  showCollapseToggle = true,
  mobile = false,
  onMobileClose,
}: SidebarContentProps) {
  const tierInfo = tier !== null ? autonomyTierDisplay(tier) : null;
  const { resolvedTheme } = useTheme();
  const iconSrc =
    resolvedTheme === "light"
      ? "/opsmender_icon_light_transparent.png"
      : "/opsmender_icon_dark_transparent.png";
  const wordmarkSrc =
    resolvedTheme === "light"
      ? "/opsmender_wordmark_light_transparent_clean.png"
      : "/opsmender_wordmark_dark_transparent_clean.png";

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-4 h-16">
        <Image
          src={iconSrc}
          alt="OpsMender"
          width={32}
          height={32}
          className="shrink-0"
        />
        {!collapsed && (
          <Image
            src={wordmarkSrc}
            alt="OpsMender"
            width={577}
            height={117}
            className="h-6 w-auto"
            priority
          />
        )}
        {mobile && onMobileClose && (
          <button
            type="button"
            onClick={onMobileClose}
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg-primary"
            title="Close navigation"
          >
            <X size={16} />
          </button>
        )}
      </div>

      {tierInfo && (
        <div className="border-b border-border-subtle px-3 py-2">
          {collapsed ? (
            <div
              title={`Tier ${tier} · ${tierInfo.label}`}
              className={`mx-auto flex h-6 w-6 items-center justify-center rounded-md border font-mono text-[11px] font-semibold ${tierInfo.className}`}
            >
              T{tier}
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wide text-fg-muted">
                Tier
              </span>
              <span
                className={`inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tierInfo.className}`}
              >
                <span className="font-mono">T{tier}</span>
                <span>{tierInfo.label}</span>
              </span>
            </div>
          )}
        </div>
      )}

      <nav className="flex-1 space-y-1 px-2 py-3 overflow-y-auto">
        {collapsed
          ? flatVisibleItems.map(({ href, label, icon: Icon, badge, exact }) =>
              renderNavLink({
                href,
                label,
                Icon,
                badge,
                active: exact ? pathname === href : pathname.startsWith(href),
                collapsed: true,
                onClick: onNavigate,
              }),
            )
          : visibleGroups.map((group) => (
              <div key={group.id} className="mb-2">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
                  {group.label}
                </div>
                <div className="space-y-0.5">
                  {group.items.map(({ href, label, icon: Icon, badge, exact }) =>
                    renderNavLink({
                      href,
                      label,
                      Icon,
                      badge,
                      active: exact ? pathname === href : pathname.startsWith(href),
                      collapsed: false,
                      onClick: onNavigate,
                    }),
                  )}
                </div>
              </div>
            ))}
      </nav>

      {showCollapseToggle && onToggleCollapse && (
        <button
          onClick={onToggleCollapse}
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
      )}

      <div className="border-t border-border-subtle px-3 py-3">
        {user && (
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-bg-panel text-xs font-semibold text-fg-secondary uppercase">
              {(user.username ?? user.email).slice(0, 2)}
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-fg-primary">
                    {user.username ?? user.email}
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
    </>
  );
}

export function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [tier, setTier] = useState<number | null>(null);
  const previousPathnameRef = useRef(pathname);

  useEffect(() => {
    const stored = localStorage.getItem(COLLAPSE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === "1") {
      setCollapsed(true);
    } else if (stored === null && window.matchMedia("(max-width: 768px)").matches) {
      // First visit on a narrow viewport: default to icon-only so the content
      // area isn't squeezed. Operator can still expand manually.
      setCollapsed(true);
    }
    localStorage.removeItem(LEGACY_GROUP_COLLAPSE_KEY);
    setHydrated(true);
  }, []);

  const loadConfig = useCallback(() => {
    if (!user) return;
    getConfig()
      .then((c) => {
        setTier(c.tier);
      })
      .catch(() => {});
  }, [user]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig, pathname]);

  // Refresh the tier badge the moment runtime config is saved (e.g. on the
  // Config page) instead of waiting for the next navigation.
  useEffect(() => {
    window.addEventListener("opsmender:config-updated", loadConfig);
    return () => window.removeEventListener("opsmender:config-updated", loadConfig);
  }, [loadConfig]);

  useEffect(() => {
    if (hydrated) localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed, hydrated]);

  useEffect(() => {
    if (previousPathnameRef.current !== pathname && mobileOpen) {
      onMobileClose?.();
    }
    previousPathnameRef.current = pathname;
  }, [mobileOpen, onMobileClose, pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mobileOpen]);

  const width = collapsed ? "w-16" : "w-60";
  const roleClass = user ? ROLE_STYLES[user.role] ?? ROLE_STYLES.viewer : "";
  const canSeeApprovals = user?.role === "admin" || user?.role === "operator";
  const pendingApprovalsCount = usePendingApprovalsCount(canSeeApprovals);

  // Filter items by role and drop any group that ends up empty.
  const navGroups = applyApprovalsBadge(
    buildNavGroups(),
    pendingApprovalsCount,
  );
  const visibleGroups: NavGroup[] = navGroups.map((group) => ({
    ...group,
    items: group.items.filter((item) =>
      navItemVisibleForRole(item, user?.role),
    ),
  })).filter((group) => group.items.length > 0);

  // When the sidebar is fully collapsed (icon-only), groups don't render
  // their headers — we just stream the items as a flat icon list because
  // there's no room for the labels.
  const flatVisibleItems = visibleGroups.flatMap((g) => g.items);

  return (
    <>
      <aside
        className={`${width} hidden h-full shrink-0 flex-col border-r border-border-subtle bg-bg-elevated transition-[width] duration-200 sm:flex`}
      >
        <SidebarContent
          collapsed={collapsed}
          pathname={pathname}
          roleClass={roleClass}
          tier={tier}
          visibleGroups={visibleGroups}
          flatVisibleItems={flatVisibleItems}
          user={user}
          logout={logout}
          onToggleCollapse={() => setCollapsed((c) => !c)}
        />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 sm:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/60"
            aria-label="Close navigation"
            onClick={onMobileClose}
          />
          <aside className="relative z-10 flex h-full w-full max-w-full flex-col border-r border-border-subtle bg-bg-elevated shadow-2xl">
            <SidebarContent
              collapsed={false}
              pathname={pathname}
              roleClass={roleClass}
              tier={tier}
              visibleGroups={visibleGroups}
              flatVisibleItems={flatVisibleItems}
              user={user}
              logout={logout}
              mobile
              onMobileClose={onMobileClose}
              onNavigate={onMobileClose}
              showCollapseToggle={false}
            />
          </aside>
        </div>
      )}
    </>
  );
}
