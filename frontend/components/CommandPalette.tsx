"use client";

/**
 * Sprint 61 (UX direction "Sprint E") Step 1 — Command Palette.
 *
 * Global Cmd+K / Ctrl+K opens a modal palette with type-to-filter
 * search across two categories: Navigate and Actions. Arrow keys
 * move through the results, Enter executes the highlighted item,
 * Esc closes.
 *
 * Mounted globally in DashboardLayout next to KeyboardShortcuts so
 * the existing Alt+key navigation keeps working. The palette does
 * not duplicate the Alt+key shortcuts — operators who like
 * one-handed nav still have them. Cmd+K is for the discovery /
 * fuzzy-search use case ("I forget the route, just take me there").
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  Bell,
  Brain,
  Building2,
  CheckSquare,
  Cpu,
  FileText,
  GitBranch,
  LayoutDashboard,
  Network,
  Phone,
  Plus,
  Repeat,
  Search,
  Server,
  Settings,
  Sparkles,
  UserCog,
  Users,
  Workflow,
  Wrench,
} from "lucide-react";

type CommandKind = "navigate" | "action";

interface CommandItem {
  id: string;
  kind: CommandKind;
  label: string;
  hint?: string;
  icon: typeof Search;
  /** Navigation target. Mutually exclusive with `run`. */
  href?: string;
  /** Programmatic action. Mutually exclusive with `href`. */
  run?: () => void;
  /** Free-text matchable beyond the label. */
  keywords?: string;
}

const NAVIGATE_ITEMS: Omit<CommandItem, "kind">[] = [
  { id: "n-dashboard", label: "Dashboard", icon: LayoutDashboard, href: "/dashboard", keywords: "home attention queue" },
  { id: "n-incidents", label: "Incidents", icon: AlertTriangle, href: "/dashboard/incidents" },
  { id: "n-approvals", label: "Approvals", icon: CheckSquare, href: "/dashboard/approvals", keywords: "pending tier" },
  { id: "n-paging", label: "Paging — Teams", icon: Users, href: "/dashboard/paging/teams" },
  { id: "n-escalation-chains", label: "Paging — Escalation Chains", icon: GitBranch, href: "/dashboard/paging/escalation-chains" },
  { id: "n-services", label: "Paging — Services", icon: Server, href: "/dashboard/paging/services" },
  { id: "n-rosters", label: "Paging — Rosters", icon: Repeat, href: "/dashboard/paging/rosters", keywords: "on-call schedule" },
  { id: "n-maintenance", label: "Paging — Maintenance Windows", icon: Wrench, href: "/dashboard/paging/maintenance-windows" },
  { id: "n-notifications", label: "Paging — Notifications", icon: Bell, href: "/dashboard/paging/notifications", keywords: "operator delivery viewer updates quiet hours routing chat" },
  { id: "n-skills", label: "AI Agent — Skills", icon: FileText, href: "/dashboard/skills" },
  { id: "n-memories", label: "AI Agent — Memories", icon: Brain, href: "/dashboard/memories" },
  { id: "n-mcp", label: "AI Agent — MCP Servers", icon: Network, href: "/dashboard/mcp-servers" },
  { id: "n-models", label: "AI Agent — Models", icon: Cpu, href: "/dashboard/models" },
  { id: "n-workflows", label: "Advanced — Session Profiles", icon: Workflow, href: "/dashboard/workflows" },
  { id: "n-reliability", label: "Observe — Reliability", icon: Activity, href: "/dashboard/reliability", keywords: "sla mttr uptime" },
  { id: "n-activity", label: "Observe — Activity", icon: Activity, href: "/dashboard/activity", keywords: "audit log" },
  { id: "n-people", label: "Admin — People", icon: UserCog, href: "/dashboard/people", keywords: "users invites" },
  { id: "n-organizations", label: "Admin — Organizations", icon: Building2, href: "/dashboard/organizations" },
  { id: "n-config", label: "Admin — Config", icon: Settings, href: "/dashboard/config", keywords: "runtime retention" },
];

const ACTION_ITEMS: Omit<CommandItem, "kind">[] = [
  {
    id: "a-new-incident",
    label: "New incident",
    hint: "Open the create-incident modal on Incidents.",
    icon: Plus,
    href: "/dashboard/incidents?new=1",
    keywords: "create open",
  },
  {
    id: "a-fire-test",
    label: "Fire test incident",
    hint: "Create a synthetic incident; AI auto-start requires an allowed T0 policy.",
    icon: Sparkles,
    href: "/dashboard/incidents?test=1",
    keywords: "drill synthetic",
  },
  {
    id: "a-pending-approvals",
    label: "Open pending approvals",
    icon: CheckSquare,
    href: "/dashboard/approvals",
    keywords: "approve reject tier 1",
  },
  {
    id: "a-on-call",
    label: "Show who's on-call now",
    hint: "Jump to the rosters page.",
    icon: Phone,
    href: "/dashboard/paging/rosters",
  },
];

function buildItems(): CommandItem[] {
  return [
    ...NAVIGATE_ITEMS.map((i) => ({ ...i, kind: "navigate" as const })),
    ...ACTION_ITEMS.map((i) => ({ ...i, kind: "action" as const })),
  ];
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

function filterItems(items: CommandItem[], query: string): CommandItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) => {
    const hay =
      `${it.label} ${it.hint ?? ""} ${it.keywords ?? ""}`.toLowerCase();
    // Cheap fuzzy: every space-separated token must appear somewhere in
    // the haystack. Good enough for a known item list.
    return q.split(/\s+/).every((tok) => hay.includes(tok));
  });
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(buildItems, []);
  const filtered = useMemo(() => filterItems(items, query), [items, query]);

  // Cmd+K / Ctrl+K toggle. Esc closes. The global listener is added
  // even when the palette is closed so the open shortcut works
  // anywhere in the dashboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isToggle =
        (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isToggle) {
        // Even when focused in an input, Cmd+K should open the
        // palette — that's the whole point of the shortcut.
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }
      if (!open) return;
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Reset query + highlight + focus the search input on open.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHighlight(0);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  }, [open]);

  // Clamp highlight when the filtered set shrinks.
  useEffect(() => {
    if (highlight >= filtered.length) setHighlight(0);
  }, [filtered.length, highlight]);

  const execute = useCallback(
    (item: CommandItem) => {
      setOpen(false);
      if (item.run) {
        item.run();
        return;
      }
      if (item.href) {
        router.push(item.href);
      }
    },
    [router],
  );

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.max(0, filtered.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[highlight];
      if (item) execute(item);
    }
  };

  // Group filtered items by kind so the rendered list keeps a stable
  // visual hierarchy (Navigate first, Actions second). Highlight
  // index is over the flat filtered array so it matches the order
  // we render in.
  const groups = useMemo(() => {
    const navigate = filtered.filter((i) => i.kind === "navigate");
    const action = filtered.filter((i) => i.kind === "action");
    return { navigate, action };
  }, [filtered]);

  if (!open) return null;

  // Compute per-item flat indices so the keyboard highlight maps to
  // the same row the user sees.
  let flatIdx = -1;

  return (
    <div
      role="dialog"
      aria-label="Command palette"
      aria-modal="true"
      data-testid="command-palette"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[10vh] backdrop-blur-sm"
      onClick={(e) => {
        // Outside-click closes; clicks inside the panel are stopped
        // below.
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-3">
          <Search size={16} className="text-fg-muted" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Search routes, actions, or settings…"
            className="flex-1 bg-transparent text-sm text-fg-primary outline-none placeholder:text-fg-muted"
            aria-label="Command palette search"
            data-testid="command-palette-input"
          />
          <kbd className="rounded border border-border-subtle bg-bg-elevated px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
            esc
          </kbd>
        </div>
        <div
          className="max-h-[60vh] overflow-y-auto px-2 py-2"
          role="listbox"
        >
          {filtered.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-fg-muted">
              No matches. Try fewer keywords.
            </p>
          ) : (
            <>
              {groups.navigate.length > 0 && (
                <Group title="Navigate">
                  {groups.navigate.map((it) => {
                    flatIdx += 1;
                    return (
                      <Row
                        key={it.id}
                        item={it}
                        active={flatIdx === highlight}
                        onClick={() => execute(it)}
                        onHover={() => setHighlight(flatIdx)}
                      />
                    );
                  })}
                </Group>
              )}
              {groups.action.length > 0 && (
                <Group title="Actions">
                  {groups.action.map((it) => {
                    flatIdx += 1;
                    return (
                      <Row
                        key={it.id}
                        item={it}
                        active={flatIdx === highlight}
                        onClick={() => execute(it)}
                        onHover={() => setHighlight(flatIdx)}
                      />
                    );
                  })}
                </Group>
              )}
            </>
          )}
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-border-subtle bg-bg-elevated/60 px-4 py-2 text-[10px] text-fg-muted">
          <span className="inline-flex items-center gap-1.5">
            <kbd className="rounded border border-border-subtle bg-bg-panel px-1.5 py-0.5 font-mono">
              ↑↓
            </kbd>
            move
            <kbd className="rounded border border-border-subtle bg-bg-panel px-1.5 py-0.5 font-mono">
              ↵
            </kbd>
            select
          </span>
          <span className="inline-flex items-center gap-1.5">
            <kbd className="rounded border border-border-subtle bg-bg-panel px-1.5 py-0.5 font-mono">
              ⌘
            </kbd>
            <kbd className="rounded border border-border-subtle bg-bg-panel px-1.5 py-0.5 font-mono">
              K
            </kbd>
            to toggle
          </span>
        </div>
      </div>
    </div>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-2 last:mb-0">
      <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
        {title}
      </p>
      <ul>{children}</ul>
    </div>
  );
}

function Row({
  item,
  active,
  onClick,
  onHover,
}: {
  item: CommandItem;
  active: boolean;
  onClick: () => void;
  onHover: () => void;
}) {
  const Icon = item.icon;
  return (
    <li>
      <button
        type="button"
        role="option"
        aria-selected={active}
        onClick={onClick}
        onMouseEnter={onHover}
        className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
          active
            ? "bg-accent-bg/40 text-fg-primary"
            : "text-fg-secondary hover:bg-bg-hover"
        }`}
      >
        <Icon size={14} className="shrink-0 text-fg-muted" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-fg-primary">{item.label}</p>
          {item.hint && (
            <p className="truncate text-[11px] text-fg-muted">{item.hint}</p>
          )}
        </div>
        <ArrowRight size={12} className="shrink-0 text-fg-muted" />
      </button>
    </li>
  );
}
