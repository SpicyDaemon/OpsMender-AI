"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bell, ChevronDown, Keyboard, LogOut, Search } from "lucide-react";
import { useAuth } from "@/context/auth";
import { listApprovals } from "@/lib/api";

type SearchKind = "incident" | "session";

const ROLE_STYLES: Record<string, string> = {
  admin: "bg-status-info-bg text-status-info border-status-info-border",
  operator: "bg-status-low-bg text-status-low border-status-low-border",
  viewer: "bg-status-neutral-bg text-status-neutral border-status-neutral-border",
};

const POLL_MS = 30_000;

export function TopBar() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const [kind, setKind] = useState<SearchKind>("incident");
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

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
      if (!menuRef.current) return;
      if (!menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const id = query.trim();
    if (!id) return;
    const path = kind === "incident" ? "incidents" : "sessions";
    router.push(`/dashboard/${path}/detail?id=${encodeURIComponent(id)}`);
  }

  const roleClass = user ? ROLE_STYLES[user.role] ?? ROLE_STYLES.viewer : "";
  const pendingLabel = pending === null ? "?" : pending > 99 ? "99+" : String(pending);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border-subtle bg-bg-elevated px-4">
      <form onSubmit={handleSubmit} className="flex flex-1 items-center gap-2 max-w-xl">
        <div className="flex flex-1 items-center gap-2 rounded-md border border-border-subtle bg-bg-input px-2.5 py-1.5 focus-within:border-border-focus">
          <Search size={14} className="shrink-0 text-fg-muted" />
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as SearchKind)}
            className="bg-transparent text-xs font-medium uppercase tracking-wide text-fg-secondary outline-none"
            aria-label="Search kind"
          >
            <option value="incident">Incident</option>
            <option value="session">Session</option>
          </select>
          <span className="h-4 w-px bg-border-subtle" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={kind === "incident" ? "Incident ID…" : "Session ID…"}
            className="flex-1 bg-transparent font-mono text-sm text-fg-primary placeholder:text-fg-muted outline-none"
          />
          {query && (
            <kbd className="hidden sm:inline rounded-sm border border-border-subtle bg-bg-panel px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
              ↵
            </kbd>
          )}
        </div>
      </form>

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => window.dispatchEvent(new CustomEvent("aim:open-shortcuts"))}
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
