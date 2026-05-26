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
  CheckCircle2,
  ChevronRight,
  Clock,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import {
  listApprovals,
  listIncidents,
  listSessions,
} from "@/lib/api";
import type {
  ApprovalRequestResponse,
  IncidentResponse,
  SessionResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { SetupChecklist } from "@/components/SetupChecklist";
import { useToast } from "@/components/ui/Toast";

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
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [incRes, apprRes, activeRes, awaitingRes, failedRes, timedOutRes] =
        await Promise.all([
          listIncidents({ limit: 200 }),
          listApprovals({ status: "pending", limit: 50 }),
          listSessions({ status: "active", limit: 25 }),
          listSessions({ status: "awaiting_approval", limit: 25 }),
          listSessions({ status: "failed", limit: 10 }),
          listSessions({ status: "timed_out", limit: 10 }),
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

  return (
    <div className="mx-auto max-w-7xl">
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
          <p className="px-2 py-3 text-xs text-fg-muted">Loading…</p>
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
