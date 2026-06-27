"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bell,
  CheckCheck,
  CheckCircle2,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  UserCog,
} from "lucide-react";
import {
  connectNotificationStream,
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type Notification,
} from "@/lib/api";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";

const PANEL_LIMIT = 10;
const POLL_MS = 60_000;

const CATEGORY_ICON: Record<string, typeof Bell> = {
  incident: AlertTriangle,
  approval: ShieldCheck,
  session: Sparkles,
  mention: MessageSquare,
  reliability: CheckCircle2,
  account: UserCog,
};

function categoryIcon(category: string) {
  return CATEGORY_ICON[category] ?? Bell;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.max(0, Math.floor(diff / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

export function NotificationBell() {
  const navigateDashboard = useDashboardNavigation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listNotifications({ limit: PANEL_LIMIT });
      setItems(res.items);
      setUnread(res.unread);
    } catch {
      // leave existing state on failure
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCount = useCallback(async () => {
    try {
      const res = await getUnreadCount();
      setUnread(res.unread);
    } catch {
      // ignore
    }
  }, []);

  // On mount: unread count + recent list.
  useEffect(() => {
    loadCount();
    loadList();
  }, [loadCount, loadList]);

  // Live socket: prepend new notifications and bump the badge.
  useEffect(() => {
    let ws: WebSocket | null = null;
    try {
      ws = connectNotificationStream({
        onNotification: (n) => {
          setItems((prev) => {
            if (prev.some((p) => p.id === n.id)) return prev;
            return [n, ...prev].slice(0, PANEL_LIMIT);
          });
          if (!n.read_at) setUnread((u) => u + 1);
        },
      });
    } catch {
      // socket unavailable — polling below covers it
    }
    return () => {
      ws?.close();
    };
  }, []);

  // Poll the unread count every 60s as a fallback when the socket drops.
  useEffect(() => {
    const t = setInterval(loadCount, POLL_MS);
    return () => clearInterval(t);
  }, [loadCount]);

  // Click-outside to close (mirrors TopBar's dropdown).
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function toggleOpen() {
    setOpen((o) => {
      const next = !o;
      if (next) loadList();
      return next;
    });
  }

  function handleItemClick(item: Notification) {
    if (!item.read_at) {
      // Optimistic mark-read.
      setItems((prev) =>
        prev.map((p) =>
          p.id === item.id ? { ...p, read_at: new Date().toISOString() } : p,
        ),
      );
      setUnread((u) => Math.max(0, u - 1));
      markNotificationRead(item.id, true).catch(() => {
        // revert badge on failure
        loadCount();
      });
    }
    setOpen(false);
    if (item.link) navigateDashboard(item.link);
  }

  async function handleMarkAll() {
    const prevItems = items;
    const prevUnread = unread;
    const now = new Date().toISOString();
    setItems((prev) =>
      prev.map((p) => (p.read_at ? p : { ...p, read_at: now })),
    );
    setUnread(0);
    try {
      await markAllNotificationsRead();
    } catch {
      setItems(prevItems);
      setUnread(prevUnread);
    }
  }

  const badgeLabel = unread > 9 ? "9+" : String(unread);

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={toggleOpen}
        title={`Inbox · ${unread} unread`}
        aria-label={`Inbox, ${unread} unread`}
        aria-haspopup="true"
        aria-expanded={open}
        className="relative flex h-9 w-9 items-center justify-center rounded-md text-fg-secondary hover:bg-bg-hover hover:text-fg-primary transition-colors"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex min-w-[18px] items-center justify-center rounded-pill border border-bg-elevated bg-status-critical px-1 font-mono text-[10px] font-semibold leading-4 text-fg-primary">
            {badgeLabel}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-96 max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-md border border-border-subtle bg-bg-panel shadow-xl">
          <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2.5">
            <span className="text-sm font-semibold text-fg-primary">
              Inbox
            </span>
            <button
              type="button"
              onClick={handleMarkAll}
              disabled={unread === 0}
              className="inline-flex items-center gap-1 text-xs font-medium text-fg-secondary hover:text-fg-primary transition-colors disabled:opacity-40 disabled:hover:text-fg-secondary"
            >
              <CheckCheck size={13} />
              Mark all read
            </button>
          </div>

          <ul className="max-h-96 overflow-y-auto py-1">
            {loading && items.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-fg-muted">
                Loading…
              </li>
            ) : items.length === 0 ? (
              <li className="px-3 py-8 text-center text-sm text-fg-muted">
                You&apos;re all caught up.
              </li>
            ) : (
              items.map((item) => {
                const Icon = categoryIcon(item.category);
                const isUnread = !item.read_at;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => handleItemClick(item)}
                      className={`flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-bg-hover ${
                        isUnread ? "bg-accent-bg/40" : ""
                      }`}
                    >
                      <Icon
                        size={15}
                        className="mt-0.5 shrink-0 text-fg-muted"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-fg-primary">
                          {item.title}
                        </p>
                        {item.body && (
                          <p className="mt-0.5 line-clamp-2 text-xs text-fg-secondary">
                            {item.body}
                          </p>
                        )}
                        <p className="mt-0.5 text-[11px] text-fg-muted">
                          {timeAgo(item.created_at)}
                        </p>
                      </div>
                      {isUnread && (
                        <span
                          aria-label="unread"
                          className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-status-info"
                        />
                      )}
                    </button>
                  </li>
                );
              })
            )}
          </ul>

          <Link
            href="/dashboard/notifications"
            onClick={() => setOpen(false)}
            className="block border-t border-border-subtle px-3 py-2.5 text-center text-xs font-medium text-accent hover:bg-bg-hover transition-colors"
          >
            View all
          </Link>
        </div>
      )}
    </div>
  );
}
