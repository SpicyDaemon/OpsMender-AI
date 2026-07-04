"use client";

/**
 * v2 Phase 3 — Session orchestration overview.
 *
 * Read-only view of AI-session capacity: per-model concurrency occupancy,
 * the currently running sessions, and the priority queue (with the reason
 * each session is waiting). Admin + operator.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronUp, ChevronDown, Trash2, Zap } from "lucide-react";
import {
  cancelQueuedSession,
  forceStartQueuedSession,
  getSessionOrchestration,
  purgeSessionQueue,
  reprioritizeQueuedSession,
} from "@/lib/api";
import type { OrchestrationOverview, OrchestrationSession } from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Button } from "@/components/ui/Button";
import { formatRelative } from "@/lib/formatDate";

function relTime(iso: string | null): string {
  if (!iso) return "—";
  return formatRelative(iso);
}

// A running AI session lasting well beyond any tier session limit (Tier 0's
// default hard cap is ~10 min) is almost certainly a stuck/zombie session.
// The orchestration payload doesn't carry the per-tier limit, so we flag on a
// conservative wall-clock threshold that no healthy session should reach.
const STALE_AFTER_MS = 60 * 60 * 1000; // 1 hour

function isStaleRunning(s: OrchestrationSession): boolean {
  return new Date(s.started_at).getTime() < Date.now() - STALE_AFTER_MS;
}

function priorityTone(p: string | null): string {
  switch (p) {
    case "P0":
      return "bg-status-critical-bg text-status-critical border-status-critical-border";
    case "P1":
      return "bg-status-high-bg text-status-high border-status-high-border";
    case "P2":
      return "bg-status-medium-bg text-status-medium border-status-medium-border";
    default:
      return "bg-bg-elevated text-fg-muted border-border-subtle";
  }
}

function SessionLink({ s }: { s: OrchestrationSession }) {
  const label = s.incident_title ?? "(no incident)";
  if (!s.incident_id) return <span className="text-fg-secondary">{label}</span>;
  return (
    <Link
      href={`/dashboard/incidents/detail?id=${s.incident_id}`}
      className="text-accent-text hover:underline"
    >
      {label}
    </Link>
  );
}

export default function OrchestrationPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canManage = isAdmin || user?.role === "operator";
  const [data, setData] = useState<OrchestrationOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getSessionOrchestration());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  const runAction = useCallback(
    async (key: string, fn: () => Promise<unknown>) => {
      setBusy(key);
      setError(null);
      try {
        await fn();
        await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Action failed");
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  async function handlePurge() {
    if (!confirm("Cancel ALL queued AI sessions? This cannot be undone.")) return;
    await runAction("purge", purgeSessionQueue);
  }

  useEffect(() => {
    reload().catch(() => {});
  }, [reload]);

  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") void reload();
    };
    const interval = window.setInterval(refreshIfVisible, 30_000);
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [reload]);

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-fg-primary">Session Orchestration</h1>
          <p className="text-sm text-fg-muted">
            Per-model AI-session capacity, running sessions, and the priority queue.
          </p>
        </div>
        <Button variant="secondary" onClick={() => reload()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-status-critical-border bg-status-critical-bg/40 p-3 text-sm text-status-critical">
          {error}
        </div>
      )}

      {/* Per-model occupancy */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-4">
        <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-fg-secondary">
          Model capacity
        </h2>
        {data && data.models.length === 0 && (
          <p className="text-sm text-fg-muted">No active models configured.</p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data?.models.map((m) => {
            const cap = m.max_concurrent_sessions ?? 0;
            const unlimited = !cap;
            const pct = unlimited ? 0 : Math.min(100, Math.round((m.running / cap) * 100));
            const full = !unlimited && m.running >= cap;
            return (
              <div
                key={m.model_config_id}
                data-testid="model-capacity-row"
                className="rounded-lg border border-border-subtle bg-bg-elevated p-3"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium text-fg-primary" title={m.name}>
                    {m.name}
                  </span>
                  <span
                    className={`shrink-0 font-mono text-xs tabular-nums ${
                      full ? "text-status-critical" : "text-fg-secondary"
                    }`}
                  >
                    {m.running}/{unlimited ? "∞" : cap}
                  </span>
                </div>
                <p className="mt-0.5 truncate font-mono text-[10px] text-fg-muted" title={m.model_id}>
                  {m.provider} · {m.model_id}
                </p>
                {!unlimited && (
                  <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-bg-panel">
                    <div
                      className={`h-full rounded-full ${full ? "bg-status-critical" : "bg-accent"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* Queued sessions */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-fg-secondary">
            Queue {data ? `(${data.queued_total})` : ""}
          </h2>
          {isAdmin && data && data.queued_total > 0 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={handlePurge}
              disabled={busy === "purge"}
            >
              <Trash2 size={13} /> Purge queue
            </Button>
          )}
        </div>
        {data && data.queued_sessions.length === 0 ? (
          <p className="text-sm text-fg-muted">No sessions are waiting on capacity.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-fg-muted">
                <th className="pb-2 font-medium">Incident</th>
                <th className="pb-2 font-medium">Priority</th>
                <th className="pb-2 font-medium">Waiting</th>
                <th className="pb-2 font-medium">Expires</th>
                <th className="pb-2 font-medium">Reason</th>
                {canManage && <th className="pb-2 font-medium text-right">Actions</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle/50">
              {data?.queued_sessions.map((s) => (
                <tr key={s.session_id} data-testid="queued-session-row">
                  <td className="py-2 pr-3"><SessionLink s={s} /></td>
                  <td className="py-2 pr-3">
                    <span className={`inline-flex rounded-pill border px-1.5 py-0.5 text-[10px] font-semibold ${priorityTone(s.priority)}`}>
                      {s.priority ?? "—"}
                      {s.queue_rank != null && (
                        <span className="ml-1 opacity-70" title="Manually reprioritized">★</span>
                      )}
                    </span>
                  </td>
                  <td className="py-2 pr-3 tabular-nums text-fg-secondary">{relTime(s.queued_at)}</td>
                  <td className="py-2 pr-3 tabular-nums text-fg-muted">{relTime(s.queue_expires_at)}</td>
                  <td className="py-2 text-fg-muted">{s.queue_reason ?? "—"}</td>
                  {canManage && (
                    <td className="py-2 text-right">
                      <div className="inline-flex items-center gap-1">
                        {isAdmin && (
                          <>
                            <button
                              type="button"
                              title="Move to front"
                              disabled={busy === s.session_id}
                              onClick={() =>
                                runAction(s.session_id, () =>
                                  reprioritizeQueuedSession(s.session_id, "front"),
                                )
                              }
                              className="rounded p-1 text-fg-muted hover:bg-bg-hover hover:text-fg-primary"
                            >
                              <ChevronUp size={14} />
                            </button>
                            <button
                              type="button"
                              title="Move to back"
                              disabled={busy === s.session_id}
                              onClick={() =>
                                runAction(s.session_id, () =>
                                  reprioritizeQueuedSession(s.session_id, "back"),
                                )
                              }
                              className="rounded p-1 text-fg-muted hover:bg-bg-hover hover:text-fg-primary"
                            >
                              <ChevronDown size={14} />
                            </button>
                            <button
                              type="button"
                              title="Force-start now (bypasses capacity)"
                              disabled={busy === s.session_id}
                              onClick={() => {
                                if (
                                  confirm(
                                    "Force-start this session now? It bypasses the model's concurrency cap.",
                                  )
                                )
                                  runAction(s.session_id, () =>
                                    forceStartQueuedSession(s.session_id),
                                  );
                              }}
                              className="rounded p-1 text-status-medium hover:bg-bg-hover"
                            >
                              <Zap size={14} />
                            </button>
                          </>
                        )}
                        <button
                          type="button"
                          title="Cancel (remove from queue)"
                          disabled={busy === s.session_id}
                          onClick={() =>
                            runAction(s.session_id, () =>
                              cancelQueuedSession(s.session_id),
                            )
                          }
                          className="rounded p-1 text-fg-muted hover:bg-bg-hover hover:text-status-critical"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Running sessions */}
      <section className="rounded-xl border border-border-subtle bg-bg-panel p-4">
        <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-fg-secondary">
          Running {data ? `(${data.active_total})` : ""}
        </h2>
        {data && data.active_sessions.length === 0 ? (
          <p className="text-sm text-fg-muted">No active AI sessions.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-fg-muted">
                <th className="pb-2 font-medium">Incident</th>
                <th className="pb-2 font-medium">Priority</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Model</th>
                <th className="pb-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle/50">
              {data?.active_sessions.map((s) => (
                <tr key={s.session_id} data-testid="active-session-row">
                  <td className="py-2 pr-3"><SessionLink s={s} /></td>
                  <td className="py-2 pr-3">
                    <span className={`inline-flex rounded-pill border px-1.5 py-0.5 text-[10px] font-semibold ${priorityTone(s.priority)}`}>
                      {s.priority ?? "—"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-fg-secondary">
                    {s.status === "awaiting_approval" ? "Awaiting approval" : "Active"}
                    {s.force_started && (
                      <span className="ml-1 text-[10px] text-status-medium" title="Force-started past capacity">
                        (forced)
                      </span>
                    )}
                    {isStaleRunning(s) && (
                      <span
                        className="ml-1.5 inline-flex rounded-pill border border-status-high-border bg-status-high-bg px-1.5 py-0.5 text-[10px] font-semibold text-status-high"
                        title="Running far longer than any tier session limit — likely stuck; consider stopping it"
                      >
                        Stale
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 font-mono text-[11px] text-fg-muted">{s.model_name ?? "—"}</td>
                  <td className="py-2 tabular-nums text-fg-muted">{relTime(s.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
