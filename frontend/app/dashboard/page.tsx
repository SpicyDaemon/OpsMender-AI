"use client";

/**
 * Sprint 59 (UX direction "Sprint C") Step 1 — Operations Dashboard.
 *
 * Replaces the historical "redirect to /dashboard/incidents" behavior
 * with a real index page that immediately answers the questions an
 * on-call operator opens the app to answer:
 *
 *   1. What is on fire right now?
 *   2. What needs approval?
 *   3. What is the AI currently doing?
 *   4. What just failed?
 *
 * Subsequent steps add the rest of the modules from the UX direction
 * doc (On-call coverage, Service health, Recent activity, MTTA/MTTR
 * summary). This first cut is the Attention Queue plus the existing
 * SetupChecklist for fresh installs.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  Bot,
  CalendarOff,
  CheckCircle2,
  ChevronRight,
  Clock,
  RefreshCw,
  ShieldAlert,
  UserCircle2,
  Users,
  XCircle,
} from "lucide-react";
import {
  listApprovals,
  listAudit,
  listIncidents,
  listRosters,
  listServices,
  listSessions,
  listTeams,
  listUsers,
  resolveOnCall,
} from "@/lib/api";
import type {
  ApprovalRequestResponse,
  AuditEntryResponse,
  IncidentResponse,
  RosterResponse,
  ServiceResponse,
  SessionResponse,
  TeamResponse,
  UserResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { SetupChecklist } from "@/components/SetupChecklist";
import { useToast } from "@/components/ui/Toast";

/**
 * Sprint 61 Step 3 — layout-specific skeleton rows shared by the
 * dashboard panels. Each block matches the eventual row geometry so the
 * panel doesn't shift when content arrives.
 */
function PanelRowSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <ul className="space-y-2 px-1 py-1.5" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <li
          key={i}
          className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5"
        >
          <div className="min-w-0 flex-1">
            <Skeleton height={12} width={i % 2 === 0 ? "72%" : "58%"} className="mb-1.5" />
            <Skeleton height={10} width="44%" />
          </div>
          <Skeleton height={18} width={36} className="shrink-0 rounded-full" />
        </li>
      ))}
    </ul>
  );
}

function CoverageGridSkeleton() {
  return (
    <div
      className="grid gap-2 md:grid-cols-2 xl:grid-cols-3"
      aria-hidden
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2.5"
        >
          <Skeleton height={12} width="58%" className="mb-2" />
          <Skeleton height={10} width="80%" className="mb-1.5" />
          <Skeleton height={10} width="68%" />
        </div>
      ))}
    </div>
  );
}

function LoadingHint({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-2 pb-1 pt-2 text-[11px] text-fg-muted" role="status">
      {children}
    </p>
  );
}

/**
 * Sprint 59 Step 5: format a millisecond duration into a short
 * dashboard-friendly string (e.g. "12m", "1h 23m", "3d 4h").
 */
function fmtDuration(ms: number): string {
  if (ms < 60_000) {
    const s = Math.max(1, Math.round(ms / 1000));
    return `${s}s`;
  }
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours < 24) return remMins > 0 ? `${hours}h ${remMins}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
}

/**
 * Median millisecond resolution time for incidents that were
 * resolved within the last `windowMs` milliseconds.
 * Returns null when the window has no resolved incidents.
 */
function medianResolveTime(
  incidents: IncidentResponse[],
  windowMs: number,
): { medianMs: number; count: number } | null {
  const cutoff = Date.now() - windowMs;
  const durations: number[] = [];
  for (const inc of incidents) {
    if (inc.status !== "resolved") continue;
    const resolvedAt = new Date(inc.updated_at).getTime();
    if (resolvedAt < cutoff) continue;
    const createdAt = new Date(inc.created_at).getTime();
    const dur = resolvedAt - createdAt;
    if (dur >= 0) durations.push(dur);
  }
  if (durations.length === 0) return null;
  durations.sort((a, b) => a - b);
  const mid = durations.length >> 1;
  const median =
    durations.length % 2 === 1
      ? durations[mid]
      : (durations[mid - 1] + durations[mid]) / 2;
  return { medianMs: median, count: durations.length };
}

/**
 * Median millisecond acknowledgment time (MTTA) for incidents that were
 * first acknowledged within the last `windowMs` milliseconds. Uses the
 * `acknowledged_at` stamp (time from `created_at` to first ack/take).
 * Returns null when the window has no acknowledged incidents.
 */
function medianAckTime(
  incidents: IncidentResponse[],
  windowMs: number,
): { medianMs: number; count: number } | null {
  const cutoff = Date.now() - windowMs;
  const durations: number[] = [];
  for (const inc of incidents) {
    if (!inc.acknowledged_at) continue;
    const ackedAt = new Date(inc.acknowledged_at).getTime();
    if (ackedAt < cutoff) continue;
    const createdAt = new Date(inc.created_at).getTime();
    const dur = ackedAt - createdAt;
    if (dur >= 0) durations.push(dur);
  }
  if (durations.length === 0) return null;
  durations.sort((a, b) => a - b);
  const mid = durations.length >> 1;
  const median =
    durations.length % 2 === 1
      ? durations[mid]
      : (durations[mid - 1] + durations[mid]) / 2;
  return { medianMs: median, count: durations.length };
}

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function DashboardIndex() {
  const toast = useToast();
  const [incidents, setIncidents] = useState<IncidentResponse[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequestResponse[]>([]);
  const [activeSessions, setActiveSessions] = useState<SessionResponse[]>([]);
  const [failedSessions, setFailedSessions] = useState<SessionResponse[]>([]);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [rosters, setRosters] = useState<RosterResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [blockedAudits, setBlockedAudits] = useState<AuditEntryResponse[]>([]);
  // Map of rosterId -> resolved user_id (null when no one is on call).
  const [onCallByRoster, setOnCallByRoster] = useState<
    Map<string, string | null>
  >(new Map());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [
        incRes,
        apprRes,
        activeRes,
        awaitingRes,
        failedRes,
        timedOutRes,
        teamsRes,
        rostersRes,
        usersRes,
        servicesRes,
        blockedRes,
      ] = await Promise.all([
        listIncidents({ limit: 200 }),
        listApprovals({ status: "pending", limit: 50 }),
        listSessions({ status: "active", limit: 25 }),
        listSessions({ status: "awaiting_approval", limit: 25 }),
        listSessions({ status: "failed", limit: 10 }),
        listSessions({ status: "timed_out", limit: 10 }),
        listTeams().catch(() => ({ items: [], total: 0 })),
        listRosters().catch(() => ({ items: [], total: 0 })),
        listUsers().catch(() => ({ items: [], total: 0 })),
        listServices().catch(() => ({ items: [], total: 0 })),
        listAudit({ permitted: false, limit: 25 }).catch(() => ({
          items: [],
          total: 0,
        })),
      ]);
      setIncidents(incRes.items);
      setApprovals(apprRes.items);
      setActiveSessions(
        [...activeRes.items, ...awaitingRes.items].sort((a, b) =>
          b.started_at.localeCompare(a.started_at),
        ),
      );
      setFailedSessions(
        [...failedRes.items, ...timedOutRes.items].sort((a, b) =>
          b.started_at.localeCompare(a.started_at),
        ),
      );
      setTeams(teamsRes.items);
      setRosters(rostersRes.items);
      setUsers(usersRes.items);
      setServices(servicesRes.items);
      setBlockedAudits(blockedRes.items);

      // Resolve on-call for every roster in parallel. Misses just
      // leave that roster as null in the map; the panel renders an
      // "uncovered" badge instead of breaking.
      const resolved = await Promise.all(
        rostersRes.items.map(async (r) => {
          try {
            const oc = await resolveOnCall(r.id);
            return [r.id, oc.user_id] as const;
          } catch {
            return [r.id, null] as const;
          }
        }),
      );
      setOnCallByRoster(new Map(resolved));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const criticalOpen = useMemo(
    () =>
      incidents.filter(
        (inc) =>
          inc.severity === "critical" &&
          (inc.status === "open" || inc.status === "in_progress"),
      ),
    [incidents],
  );

  const allOpen = useMemo(
    () =>
      incidents.filter(
        (inc) => inc.status === "open" || inc.status === "in_progress",
      ),
    [incidents],
  );

  // Group rosters by team for the On-call coverage panel. Teams without
  // a roster still show — operators need to know they have a coverage
  // gap. Username lookups use the loaded users list (best-effort; a
  // miss shows the short uuid instead so the row never collapses).
  const userById = useMemo(
    () => new Map(users.map((u) => [u.id, u])),
    [users],
  );
  const rostersByTeam = useMemo(() => {
    const m = new Map<string, RosterResponse[]>();
    for (const r of rosters) {
      if (!m.has(r.team_id)) m.set(r.team_id, []);
      m.get(r.team_id)!.push(r);
    }
    return m;
  }, [rosters]);
  // Sprint 59 Step 3: per-service health + noisy-services aggregations.
  // Both panels source from the same loaded incidents list so there's
  // no extra fetch. "Noisy" is incidents created in the last 24h
  // (decision: incident count, not raw alert volume).
  const teamById = useMemo(
    () => new Map(teams.map((t) => [t.id, t])),
    [teams],
  );
  const serviceStats = useMemo(() => {
    const now = Date.now();
    const window24h = now - 86_400_000;
    return services.map((svc) => {
      const own = incidents.filter((inc) => inc.service_id === svc.id);
      const openCount = own.filter(
        (inc) => inc.status === "open" || inc.status === "in_progress",
      ).length;
      const last = own.reduce<string | null>((acc, inc) => {
        const candidate = inc.updated_at ?? inc.created_at;
        if (!acc || candidate > acc) return candidate;
        return acc;
      }, null);
      const last24hCount = own.filter(
        (inc) => new Date(inc.created_at).getTime() >= window24h,
      ).length;
      return {
        service: svc,
        teamName: teamById.get(svc.team_id)?.name ?? null,
        openCount,
        lastIncidentAt: last,
        last24hCount,
      };
    });
  }, [services, incidents, teamById]);

  // Top services by open-incident count (then by last activity).
  // Hidden when no service has any open incidents — silence is good.
  const serviceHealthRows = useMemo(() => {
    return serviceStats
      .filter((row) => row.openCount > 0)
      .sort((a, b) => {
        if (a.openCount !== b.openCount) return b.openCount - a.openCount;
        return (b.lastIncidentAt ?? "").localeCompare(a.lastIncidentAt ?? "");
      })
      .slice(0, 5);
  }, [serviceStats]);

  // Top services by 24h incident count. Same hiding rule.
  const noisyServiceRows = useMemo(() => {
    return serviceStats
      .filter((row) => row.last24hCount > 0)
      .sort((a, b) => b.last24hCount - a.last24hCount)
      .slice(0, 5);
  }, [serviceStats]);

  // Sprint 59 Step 5 — MTTR rolling-window medians. MTTR uses
  // `updated_at - created_at` for incidents that hit `resolved` or
  // `resolved` within each window.
  const mttr = useMemo(() => {
    return {
      d1: medianResolveTime(incidents, 86_400_000), // 24h
      d7: medianResolveTime(incidents, 7 * 86_400_000),
      d30: medianResolveTime(incidents, 30 * 86_400_000),
    };
  }, [incidents]);

  // MTTA rolling-window medians (Sprint 59 follow-up). Time from
  // `created_at` to the `acknowledged_at` stamp, for incidents first
  // acknowledged within each window.
  const mtta = useMemo(() => {
    return {
      d1: medianAckTime(incidents, 86_400_000), // 24h
      d7: medianAckTime(incidents, 7 * 86_400_000),
      d30: medianAckTime(incidents, 30 * 86_400_000),
    };
  }, [incidents]);

  // Sprint 59 Step 4 — Recent activity feed. Scope per owner decision:
  // incident lifecycle + tool-blocks only. We synthesize the lifecycle
  // events from the incidents array (no dedicated events table) plus
  // the blocked audit entries from the parallel /audit fetch. Merge,
  // sort descending by timestamp, take the top N.
  type ActivityItem = {
    id: string;
    ts: string;
    kind: "incident_opened" | "incident_resolved" | "tool_blocked";
    primary: string;
    detail: string;
    href: string;
  };
  const activityItems = useMemo<ActivityItem[]>(() => {
    const items: ActivityItem[] = [];

    for (const inc of incidents) {
      items.push({
        id: `inc-open-${inc.id}`,
        ts: inc.created_at,
        kind: "incident_opened",
        primary: inc.title,
        detail: inc.severity ? `Severity ${inc.severity}` : "Severity not set",
        href: `/dashboard/incidents/detail?id=${inc.id}`,
      });
      if (inc.status === "resolved") {
        items.push({
          id: `inc-resolved-${inc.id}`,
          ts: inc.updated_at,
          kind: "incident_resolved",
          primary: inc.title,
          detail: `Incident ${inc.status.replace("_", " ")}`,
          href: `/dashboard/incidents/detail?id=${inc.id}`,
        });
      }
    }

    for (const a of blockedAudits) {
      items.push({
        id: `audit-${a.id}`,
        ts: a.timestamp,
        kind: "tool_blocked",
        primary: a.tool_name ?? "Blocked operation",
        detail: a.block_reason ?? "Tier gate refused the action",
        href: `/dashboard/sessions/detail?id=${a.session_id}`,
      });
    }

    items.sort((a, b) => b.ts.localeCompare(a.ts));
    return items.slice(0, 12);
  }, [incidents, blockedAudits]);

  const coverageRows = useMemo(
    () =>
      teams
        .map((team) => ({
          team,
          rosters: rostersByTeam.get(team.id) ?? [],
        }))
        // Show teams with rosters first so coverage gaps don't dominate
        // the panel at the top.
        .sort((a, b) => {
          const ar = a.rosters.length > 0 ? 0 : 1;
          const br = b.rosters.length > 0 ? 0 : 1;
          if (ar !== br) return ar - br;
          return a.team.name.localeCompare(b.team.name);
        }),
    [teams, rostersByTeam],
  );

  return (
    <div>
      <PageHeader
        title="Operations dashboard"
        subtitle="What needs your attention right now."
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={load}
            disabled={refreshing}
          >
            <RefreshCw
              size={14}
              className={refreshing ? "animate-spin" : ""}
            />
            Refresh
          </Button>
        }
      />

      <SetupChecklist />

      {/* Attention Queue — four cards at the top of the page */}
      <section
        aria-label="Attention queue"
        className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        <AttentionCard
          tone="critical"
          icon={AlertOctagon}
          label="Critical, open"
          count={criticalOpen.length}
          loading={loading}
          loadingHint="Scanning incidents…"
          emptyMessage="Nothing critical is open."
          href="/dashboard/incidents"
        >
          {criticalOpen.slice(0, 4).map((inc) => (
            <RowLink
              key={inc.id}
              href={`/dashboard/incidents/detail?id=${inc.id}`}
              title={inc.title}
              meta={`${inc.status.replace("_", " ")} · opened ${fmtRelative(inc.created_at)}`}
              accent="critical"
            />
          ))}
        </AttentionCard>

        <AttentionCard
          tone="high"
          icon={Clock}
          label="Awaiting approval"
          count={approvals.length}
          loading={loading}
          loadingHint="Checking approval inbox…"
          emptyMessage="No pending approvals."
          href="/dashboard/approvals"
        >
          {approvals.slice(0, 4).map((appr) => (
            <RowLink
              key={appr.id}
              href={`/dashboard/sessions/detail?id=${appr.session_id}`}
              title={extractToolName(appr) ?? "Pending approval"}
              meta={`requested ${fmtRelative(appr.requested_at)}`}
              accent="high"
            />
          ))}
        </AttentionCard>

        <AttentionCard
          tone="medium"
          icon={Bot}
          label="Active AI sessions"
          count={activeSessions.length}
          loading={loading}
          loadingHint="Polling agent sessions…"
          emptyMessage="No active sessions."
          href="/dashboard/incidents"
        >
          {activeSessions.slice(0, 4).map((s) => (
            <RowLink
              key={s.id}
              href={`/dashboard/sessions/detail?id=${s.id}`}
              title={`Session ${s.id.slice(0, 8)}…`}
              meta={`${s.status.replace("_", " ")} · started ${fmtRelative(s.started_at)} · Tier ${s.tier}`}
              accent="medium"
            />
          ))}
        </AttentionCard>

        <AttentionCard
          tone="critical"
          icon={XCircle}
          label="Recent failures"
          count={failedSessions.length}
          loading={loading}
          loadingHint="Looking for failed sessions…"
          emptyMessage="No recent failed or timed-out sessions."
          href="/dashboard/activity"
        >
          {failedSessions.slice(0, 4).map((s) => (
            <RowLink
              key={s.id}
              href={`/dashboard/sessions/detail?id=${s.id}`}
              title={`Session ${s.id.slice(0, 8)}…`}
              meta={`${s.status.replace("_", " ")} · ${fmtRelative(s.ended_at ?? s.started_at)}`}
              accent="critical"
            />
          ))}
        </AttentionCard>
      </section>

      {/* Secondary band — quick links + light counters */}
      <section className="mt-6 grid gap-4 sm:grid-cols-3">
        <QuickStat
          label="Open incidents"
          value={allOpen.length}
          href="/dashboard/incidents"
          icon={AlertTriangle}
        />
        <QuickStat
          label="Critical severity"
          value={incidents.filter((i) => i.severity === "critical").length}
          href="/dashboard/incidents"
          icon={ShieldAlert}
          tone="critical"
        />
        <QuickStat
          label="Resolved last 24h"
          value={
            incidents.filter(
              (i) =>
                i.status === "resolved" &&
                Date.now() - new Date(i.updated_at).getTime() < 86_400_000,
            ).length
          }
          href="/dashboard/incidents"
          icon={CheckCircle2}
          tone="low"
        />
      </section>

      {/* MTTA rolling-window tiles — Sprint 59 follow-up. Three tiles for
          24h / 7d / 30d, mirroring the MTTR family below. */}
      <section className="mt-6 rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              MTTA · median time to acknowledge
            </p>
            <p className="text-[10px] text-fg-muted">
              Time from `created_at` to first acknowledgment, by ack window.
            </p>
          </div>
        </div>
        <div className="grid gap-3 px-4 py-4 sm:grid-cols-3 sm:px-5 sm:py-5">
          {(
            [
              { label: "Last 24h", stat: mtta.d1 },
              { label: "Last 7 days", stat: mtta.d7 },
              { label: "Last 30 days", stat: mtta.d30 },
            ] as const
          ).map(({ label, stat }) => (
            <div
              key={label}
              className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3"
            >
              <p className="text-[10px] font-medium uppercase tracking-wide text-fg-muted">
                {label}
              </p>
              <p className="mt-1.5 text-2xl font-semibold tabular-nums text-fg-primary">
                {stat ? fmtDuration(stat.medianMs) : "—"}
              </p>
              <p className="mt-0.5 text-[11px] text-fg-muted">
                {stat
                  ? `${stat.count} incident${stat.count === 1 ? "" : "s"} acknowledged`
                  : "No acknowledged incidents"}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* MTTR rolling-window tiles — Sprint 59 Step 5. Three tiles for
          24h / 7d / 30d. */}
      <section className="mt-6 rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              MTTR · median time to resolve
            </p>
            <p className="text-[10px] text-fg-muted">
              Time from `created_at` to `resolved`, by resolution window.
            </p>
          </div>
        </div>
        <div className="grid gap-3 px-4 py-4 sm:grid-cols-3 sm:px-5 sm:py-5">
          {(
            [
              { label: "Last 24h", stat: mttr.d1 },
              { label: "Last 7 days", stat: mttr.d7 },
              { label: "Last 30 days", stat: mttr.d30 },
            ] as const
          ).map(({ label, stat }) => (
            <div
              key={label}
              className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3"
            >
              <p className="text-[10px] font-medium uppercase tracking-wide text-fg-muted">
                {label}
              </p>
              <p className="mt-1.5 text-2xl font-semibold tabular-nums text-fg-primary">
                {stat ? fmtDuration(stat.medianMs) : "—"}
              </p>
              <p className="mt-0.5 text-[11px] text-fg-muted">
                {stat
                  ? `${stat.count} incident${stat.count === 1 ? "" : "s"} resolved`
                  : "No resolved incidents"}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* On-call coverage — Sprint 59 Step 2. Per-team current on-call
          resolved via the team's roster(s). Teams without a roster
          surface as coverage gaps. */}
      <section className="mt-6 rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
          <div className="flex items-center gap-2">
            <Users size={14} className="text-fg-secondary" />
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              On-call coverage
            </p>
          </div>
          <Link
            href="/dashboard/paging/rosters"
            className="inline-flex items-center gap-1 text-[11px] font-medium text-fg-secondary hover:text-fg-primary"
          >
            Manage rosters <ArrowRight size={11} />
          </Link>
        </div>
        <div className="px-3 py-3 sm:px-4">
          {loading ? (
            <div>
              <LoadingHint>Resolving on-call rotations…</LoadingHint>
              <CoverageGridSkeleton />
            </div>
          ) : coverageRows.length === 0 ? (
            <p className="px-2 py-2 text-xs text-fg-muted">
              No teams yet.{" "}
              <Link
                href="/dashboard/paging/teams"
                className="font-medium text-accent hover:underline"
              >
                Create your first team
              </Link>
              .
            </p>
          ) : (
            <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {coverageRows.map(({ team, rosters: tr }) => (
                <li
                  key={team.id}
                  className="rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2.5"
                >
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <Link
                      href="/dashboard/paging/teams"
                      className="truncate text-sm font-medium text-fg-primary hover:text-accent"
                    >
                      {team.name}
                    </Link>
                  </div>
                  {tr.length === 0 ? (
                    <p className="flex items-center gap-1.5 text-[11px] text-status-medium">
                      <CalendarOff size={11} />
                      No roster — coverage gap
                    </p>
                  ) : (
                    <ul className="space-y-1">
                      {tr.map((r) => {
                        const userId = onCallByRoster.get(r.id) ?? null;
                        const user = userId ? userById.get(userId) : null;
                        return (
                          <li
                            key={r.id}
                            className="flex items-center justify-between gap-2 text-[11px]"
                          >
                            <span className="truncate text-fg-secondary">
                              {r.name}
                            </span>
                            {user ? (
                              <span className="inline-flex items-center gap-1 text-fg-primary">
                                <UserCircle2
                                  size={11}
                                  className="text-fg-muted"
                                />
                                <span className="truncate font-medium">
                                  {user.username}
                                </span>
                              </span>
                            ) : userId ? (
                              <span className="font-mono text-fg-muted">
                                {userId.slice(0, 8)}…
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-status-medium">
                                <CalendarOff size={11} />
                                Uncovered
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* Service health + Noisy services — Sprint 59 Step 3. Two
          side-by-side panels driven by the already-loaded incidents
          list. Service health = services with open incidents, sorted
          by open count then last activity. Noisy services = top 5
          services by incidents created in the last 24h (decision:
          incident count rather than raw alert volume). */}
      <section className="mt-6 grid gap-4 lg:grid-cols-2">
        <ServicePanel
          title="Service health"
          subtitle="Services with open incidents."
          icon={ShieldAlert}
          loading={loading}
          emptyMessage="No services have open incidents right now."
          rows={serviceHealthRows.map((row) => ({
            id: row.service.id,
            primary: row.service.name,
            secondary: row.teamName,
            metricLabel: "Open",
            metric: row.openCount,
            metricTone: row.openCount >= 3 ? "critical" : "high",
            footnote: row.lastIncidentAt
              ? `Last activity ${fmtRelative(row.lastIncidentAt)}`
              : "—",
          }))}
        />
        <ServicePanel
          title="Noisy services (24h)"
          subtitle="Most incidents created in the last 24 hours."
          icon={AlertTriangle}
          loading={loading}
          emptyMessage="No services produced incidents in the last 24h."
          rows={noisyServiceRows.map((row) => ({
            id: row.service.id,
            primary: row.service.name,
            secondary: row.teamName,
            metricLabel: "24h",
            metric: row.last24hCount,
            metricTone: row.last24hCount >= 5 ? "critical" : "medium",
            footnote: row.lastIncidentAt
              ? `Last incident ${fmtRelative(row.lastIncidentAt)}`
              : "—",
          }))}
        />
      </section>

      {/* Recent activity feed — Sprint 59 Step 4. Incident lifecycle
          (open / resolve / close) synthesized from the incidents
          array + tool blocks pulled from /audit?permitted=false. No
          backend changes — just merge + sort + render. */}
      <section className="mt-6 rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
          <div className="flex items-center gap-2">
            <Clock size={14} className="text-fg-secondary" />
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              Recent activity
            </p>
          </div>
          <Link
            href="/dashboard/activity"
            className="inline-flex items-center gap-1 text-[11px] font-medium text-fg-secondary hover:text-fg-primary"
          >
            Full audit log <ArrowRight size={11} />
          </Link>
        </div>
        <div className="px-3 py-2 sm:px-4">
          {loading ? (
            <div>
              <LoadingHint>Building activity feed…</LoadingHint>
              <PanelRowSkeleton rows={4} />
            </div>
          ) : activityItems.length === 0 ? (
            <p className="px-2 py-2 text-xs text-fg-muted">
              Nothing to report yet. Incidents and blocked tool calls will
              show up here as they happen.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle/60">
              {activityItems.map((it) => {
                const meta = ACTIVITY_KIND_META[it.kind];
                const KindIcon = meta.icon;
                return (
                  <li key={it.id}>
                    <Link
                      href={it.href}
                      className="group flex items-start gap-3 rounded-md px-2 py-2 hover:bg-bg-hover"
                    >
                      <KindIcon size={14} className={`mt-0.5 shrink-0 ${meta.color}`} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <p className="truncate text-sm font-medium text-fg-primary group-hover:text-accent">
                            {meta.label}: {it.primary}
                          </p>
                          <span className="shrink-0 text-[10px] tabular-nums text-fg-muted">
                            {fmtRelative(it.ts)}
                          </span>
                        </div>
                        <p className="truncate text-[11px] text-fg-muted">
                          {it.detail}
                        </p>
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

// Activity-feed kind metadata (Sprint 59 Step 4).
const ACTIVITY_KIND_META: Record<
  "incident_opened" | "incident_resolved" | "tool_blocked",
  { label: string; icon: typeof AlertOctagon; color: string }
> = {
  incident_opened: {
    label: "Incident opened",
    icon: AlertOctagon,
    color: "text-status-critical",
  },
  incident_resolved: {
    label: "Incident resolved",
    icon: CheckCircle2,
    color: "text-status-low",
  },
  tool_blocked: {
    label: "Tool blocked",
    icon: ShieldAlert,
    color: "text-status-high",
  },
};

interface ServicePanelRow {
  id: string;
  primary: string;
  secondary: string | null;
  metricLabel: string;
  metric: number;
  metricTone: "critical" | "high" | "medium" | "low";
  footnote: string;
}

function ServicePanel({
  title,
  subtitle,
  icon: Icon,
  loading,
  emptyMessage,
  rows,
}: {
  title: string;
  subtitle: string;
  icon: typeof ShieldAlert;
  loading: boolean;
  emptyMessage: string;
  rows: ServicePanelRow[];
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-fg-secondary" />
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
              {title}
            </p>
            <p className="text-[10px] text-fg-muted">{subtitle}</p>
          </div>
        </div>
        <Link
          href="/dashboard/paging/services"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-fg-secondary hover:text-fg-primary"
        >
          All services <ArrowRight size={11} />
        </Link>
      </div>
      <div className="px-3 py-3 sm:px-4">
        {loading ? (
          <div>
            <LoadingHint>Loading service health…</LoadingHint>
            <PanelRowSkeleton rows={3} />
          </div>
        ) : rows.length === 0 ? (
          <p className="px-2 py-2 text-xs text-fg-muted">{emptyMessage}</p>
        ) : (
          <ul className="space-y-1">
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-bg-hover"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-fg-primary">
                    {row.primary}
                  </p>
                  <p className="truncate text-[11px] text-fg-muted">
                    {row.secondary ? `${row.secondary} · ` : ""}
                    {row.footnote}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <Badge variant={row.metricTone}>{row.metric}</Badge>
                  <p className="mt-0.5 text-[10px] uppercase tracking-wide text-fg-muted">
                    {row.metricLabel}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

interface AttentionCardProps {
  tone: "critical" | "high" | "medium" | "low";
  icon: typeof AlertOctagon;
  label: string;
  count: number;
  loading: boolean;
  loadingHint: string;
  emptyMessage: string;
  href: string;
  children: React.ReactNode;
}

function AttentionCard({
  tone,
  icon: Icon,
  label,
  count,
  loading,
  loadingHint,
  emptyMessage,
  href,
  children,
}: AttentionCardProps) {
  const toneClasses: Record<typeof tone, string> = {
    critical: "border-status-critical-border",
    high: "border-status-high-border",
    medium: "border-status-medium-border",
    low: "border-status-low-border",
  };
  const toneText: Record<typeof tone, string> = {
    critical: "text-status-critical",
    high: "text-status-high",
    medium: "text-status-medium",
    low: "text-status-low",
  };

  return (
    <div
      className={`flex flex-col rounded-xl border bg-bg-panel shadow-sm ${toneClasses[tone]}`}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          <Icon size={14} className={toneText[tone]} />
          <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-secondary">
            {label}
          </p>
        </div>
        <Badge variant={tone}>{loading ? "…" : count}</Badge>
      </div>
      <div className="flex-1 px-2 py-2">
        {loading ? (
          <div>
            <LoadingHint>{loadingHint}</LoadingHint>
            <PanelRowSkeleton rows={3} />
          </div>
        ) : count === 0 ? (
          <p className="px-2 py-3 text-xs text-fg-muted">{emptyMessage}</p>
        ) : (
          <ul className="space-y-1">{children}</ul>
        )}
      </div>
      <Link
        href={href}
        className="flex items-center justify-between gap-2 border-t border-border-subtle px-4 py-2 text-xs font-medium text-fg-secondary transition-colors hover:bg-bg-hover hover:text-fg-primary"
      >
        View all <ArrowRight size={12} />
      </Link>
    </div>
  );
}

function RowLink({
  href,
  title,
  meta,
  accent,
}: {
  href: string;
  title: string;
  meta: string;
  accent: "critical" | "high" | "medium" | "low";
}) {
  return (
    <li>
      <Link
        href={href}
        className="group flex items-start justify-between gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-bg-hover"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-fg-primary group-hover:text-accent">
            {title}
          </p>
          <p className="truncate text-[10px] text-fg-muted">{meta}</p>
        </div>
        <ChevronRight
          size={12}
          className="mt-0.5 shrink-0 text-fg-muted group-hover:text-fg-secondary"
        />
      </Link>
    </li>
  );
}

interface QuickStatProps {
  label: string;
  value: number;
  href: string;
  icon: typeof AlertTriangle;
  tone?: "critical" | "low" | "default";
}

function QuickStat({
  label,
  value,
  href,
  icon: Icon,
  tone = "default",
}: QuickStatProps) {
  const toneText: Record<NonNullable<QuickStatProps["tone"]>, string> = {
    critical: "text-status-critical",
    low: "text-status-low",
    default: "text-fg-primary",
  };
  return (
    <Link
      href={href}
      className="flex items-center gap-4 rounded-xl border border-border-subtle bg-bg-panel px-5 py-4 shadow-sm transition-colors hover:border-border-strong"
    >
      <Icon size={20} className="text-fg-muted" />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
          {label}
        </p>
        <p className={`mt-1 text-2xl font-semibold tabular-nums ${toneText[tone]}`}>
          {value}
        </p>
      </div>
    </Link>
  );
}

// Approval rows from the backend don't carry the tool name directly;
// it sits inside the `action` JSON. Best-effort extraction.
function extractToolName(appr: ApprovalRequestResponse): string | null {
  if (!appr.action || typeof appr.action !== "object") return null;
  const action = appr.action as Record<string, unknown>;
  const tool = action.tool ?? action.tool_name;
  return typeof tool === "string" ? tool : null;
}
