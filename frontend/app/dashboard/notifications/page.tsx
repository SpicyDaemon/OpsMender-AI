"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Bell,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  Circle,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserCog,
} from "lucide-react";
import {
  deleteNotification,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type Notification,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChips } from "@/components/ui/FilterChips";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { formatDateTime } from "@/lib/formatDate";

const PAGE_SIZE = 25;

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

function fmtDate(iso: string) {
  return formatDateTime(iso);
}

type NotificationGroup = {
  key: string;
  item: Notification;
  items: Notification[];
  count: number;
  unreadCount: number;
};

function notificationGroupKey(item: Notification) {
  // Group by content only. Subject ids (incident/session/link) are
  // deliberately excluded: the flagship duplicate stack — four "Queued AI
  // session expired" rows — comes from four *different* sessions, and keying
  // on the subject would keep them apart. Per-item deep links stay reachable
  // through the expanded group.
  return JSON.stringify([
    item.event_type,
    item.category,
    item.title,
    item.body ?? "",
  ]);
}

function coalesceNotifications(items: Notification[]): NotificationGroup[] {
  const groups = new Map<string, NotificationGroup>();
  for (const item of items) {
    const key = notificationGroupKey(item);
    const existing = groups.get(key);
    if (existing) {
      existing.items.push(item);
      existing.count += 1;
      if (!item.read_at) existing.unreadCount += 1;
    } else {
      groups.set(key, {
        key,
        item,
        items: [item],
        count: 1,
        unreadCount: item.read_at ? 0 : 1,
      });
    }
  }
  return Array.from(groups.values());
}

const TABS = [
  { value: "all" as const, label: "All" },
  { value: "unread" as const, label: "Unread" },
];

type Tab = (typeof TABS)[number]["value"];

export default function NotificationsPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("all");
  const [items, setItems] = useState<Notification[]>([]);
  const [total, setTotal] = useState(0);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (reset: boolean) => {
      if (reset) setLoading(true);
      else setLoadingMore(true);
      try {
        const offset = reset ? 0 : items.length;
        const res = await listNotifications({
          unread_only: tab === "unread",
          limit: PAGE_SIZE,
          offset,
        });
        setItems((prev) => (reset ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
        setUnread(res.unread);
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Failed to load notifications",
        );
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    // items.length is read inside but we intentionally drive reloads via tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tab, toast],
  );

  useEffect(() => {
    load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function handleToggleRead(group: NotificationGroup) {
    const makeRead = group.unreadCount > 0;
    const now = new Date().toISOString();
    const ids = new Set(group.items.map((item) => item.id));
    setItems((prev) =>
      prev.map((p) =>
        ids.has(p.id) ? { ...p, read_at: makeRead ? now : null } : p,
      ),
    );
    setUnread((u) =>
      Math.max(0, u + (makeRead ? -group.unreadCount : group.count)),
    );
    try {
      await Promise.all(
        group.items.map((item) => markNotificationRead(item.id, makeRead)),
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
      load(true);
    }
  }

  async function handleDelete(group: NotificationGroup) {
    const prevItems = items;
    const ids = new Set(group.items.map((item) => item.id));
    setItems((prev) => prev.filter((p) => !ids.has(p.id)));
    setTotal((t) => Math.max(0, t - group.count));
    if (group.unreadCount > 0) {
      setUnread((u) => Math.max(0, u - group.unreadCount));
    }
    try {
      await Promise.all(group.items.map((item) => deleteNotification(item.id)));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
      setItems(prevItems);
    }
  }

  async function handleMarkAll() {
    const prevItems = items;
    const prevUnread = unread;
    const now = new Date().toISOString();
    setItems((prev) => prev.map((p) => (p.read_at ? p : { ...p, read_at: now })));
    setUnread(0);
    try {
      await markAllNotificationsRead();
      toast.success("All notifications marked read");
      if (tab === "unread") load(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to mark all read");
      setItems(prevItems);
      setUnread(prevUnread);
    }
  }

  function handleOpen(group: NotificationGroup) {
    const unreadItems = group.items.filter((item) => !item.read_at);
    if (unreadItems.length > 0) {
      const now = new Date().toISOString();
      const ids = new Set(unreadItems.map((item) => item.id));
      setItems((prev) =>
        prev.map((p) => (ids.has(p.id) ? { ...p, read_at: now } : p)),
      );
      setUnread((u) => Math.max(0, u - unreadItems.length));
      Promise.all(unreadItems.map((item) => markNotificationRead(item.id, true))).catch(() =>
        load(true),
      );
    }
  }

  /** Mark a single item read when its deep link inside an expanded group is followed. */
  function handleOpenItem(item: Notification) {
    if (item.read_at) return;
    const now = new Date().toISOString();
    setItems((prev) =>
      prev.map((p) => (p.id === item.id ? { ...p, read_at: now } : p)),
    );
    setUnread((u) => Math.max(0, u - 1));
    markNotificationRead(item.id, true).catch(() => load(true));
  }

  function toggleGroupExpanded(key: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const hasMore = items.length < total;
  const groups = coalesceNotifications(items);

  return (
    <div>
      <div className="mb-6">
        <PageHeader
          title="Inbox"
          subtitle={`${total} total · ${unread} unread`}
          icon={<Bell size={18} />}
          actions={
            <>
              <Link
                href="/dashboard/notifications/preferences"
                className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-surface px-3 py-1.5 text-sm font-medium text-fg-primary transition-colors hover:bg-bg-hover"
              >
                <SlidersHorizontal size={14} /> Preferences
              </Link>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => load(true)}
                disabled={loading}
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                Refresh
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleMarkAll}
                disabled={unread === 0}
              >
                <CheckCheck size={14} />
                Mark all read
              </Button>
            </>
          }
        />
      </div>

      <div className="mb-5">
        <FilterChips
          ariaLabel="Filter notifications"
          options={TABS}
          value={tab}
          onChange={(v) => setTab(v)}
        />
      </div>

      {loading ? (
        <TableSkeleton rows={6} columns={3} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Bell}
          title={tab === "unread" ? "No unread notifications" : "No notifications yet"}
          description={
            tab === "unread"
              ? "You're all caught up."
              : "Updates about incidents, approvals, sessions, and mentions will show up here."
          }
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <ul className="divide-y divide-border-subtle">
            {groups.map((group) => {
              const { item } = group;
              const Icon = categoryIcon(item.category);
              const isUnread = group.unreadCount > 0;
              // A grouped stack may span different subjects (e.g. four
              // expired sessions). Only hard-link the row when every item
              // points at the same place; otherwise the row expands to the
              // per-item deep links below.
              const distinctLinks = new Set(
                group.items.map((i) => i.link ?? ""),
              );
              const sharedLink =
                distinctLinks.size === 1 ? item.link : null;
              const isExpanded = expandedGroups.has(group.key);
              const content = (
                <>
                  <p className="flex items-center gap-2 text-sm font-medium text-fg-primary">
                    {isUnread && (
                      <span
                        aria-label={
                          group.unreadCount > 1
                            ? `${group.unreadCount} unread`
                            : "unread"
                        }
                        className="h-2 w-2 shrink-0 rounded-full bg-status-info"
                      />
                    )}
                    <span className="truncate">{item.title}</span>
                  </p>
                  {item.body && (
                    <p className="mt-0.5 text-xs text-fg-secondary">{item.body}</p>
                  )}
                  <p className="mt-1 font-mono text-[11px] text-fg-muted">
                    {fmtDate(item.created_at)}
                  </p>
                </>
              );
              return (
                <li
                  key={group.key}
                  className={`px-4 py-3 transition-colors ${
                    isUnread ? "bg-accent-bg/30" : ""
                  }`}
                >
                  <div className="flex items-start gap-3">
                  <Icon size={16} className="mt-0.5 shrink-0 text-fg-muted" />
                  {sharedLink ? (
                    <Link
                      href={sharedLink}
                      onClick={() => handleOpen(group)}
                      className="min-w-0 flex-1 text-left"
                    >
                      {content}
                    </Link>
                  ) : (
                    <button
                      type="button"
                      onClick={() =>
                        group.count > 1
                          ? toggleGroupExpanded(group.key)
                          : handleOpen(group)
                      }
                      className="min-w-0 flex-1 text-left"
                    >
                      {content}
                    </button>
                  )}
                  {group.count > 1 && (
                    <button
                      type="button"
                      onClick={() => toggleGroupExpanded(group.key)}
                      aria-expanded={isExpanded}
                      aria-label={`${group.count} duplicate notifications — ${
                        isExpanded ? "collapse" : "expand"
                      }`}
                      className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full border border-border-subtle bg-bg-elevated px-1.5 py-0.5 text-[10px] font-semibold text-fg-secondary transition-colors hover:bg-bg-hover hover:text-fg-primary"
                    >
                      ×{group.count}
                      <ChevronDown
                        size={10}
                        className={`transition-transform ${isExpanded ? "rotate-180" : ""}`}
                      />
                    </button>
                  )}
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleToggleRead(group)}
                      title={isUnread ? "Mark as read" : "Mark as unread"}
                      aria-label={isUnread ? "Mark as read" : "Mark as unread"}
                      className="rounded-md p-1.5 text-fg-muted hover:bg-bg-hover hover:text-fg-primary transition-colors"
                    >
                      {isUnread ? (
                        <Circle size={14} />
                      ) : (
                        <CheckCircle2 size={14} />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(group)}
                      title="Delete"
                      aria-label="Delete notification"
                      className="rounded-md p-1.5 text-fg-muted hover:bg-status-critical-bg hover:text-status-critical transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  </div>
                  {isExpanded && group.count > 1 && (
                    <ul className="mt-2 space-y-1 border-l border-border-subtle pl-7">
                      {group.items.map((sub) => (
                        <li
                          key={sub.id}
                          className="flex items-center gap-2 text-xs text-fg-secondary"
                        >
                          {!sub.read_at && (
                            <span
                              aria-label="unread"
                              className="h-1.5 w-1.5 shrink-0 rounded-full bg-status-info"
                            />
                          )}
                          <span className="font-mono tabular-nums text-fg-muted">
                            {fmtDate(sub.created_at)}
                          </span>
                          {sub.link && (
                            <Link
                              href={sub.link}
                              onClick={() => handleOpenItem(sub)}
                              className="text-accent-text hover:underline"
                            >
                              Open
                            </Link>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
          {hasMore && (
            <div className="border-t border-border-subtle p-3 text-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => load(false)}
                disabled={loadingMore}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
