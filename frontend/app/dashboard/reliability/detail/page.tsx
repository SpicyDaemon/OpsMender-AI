"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ServerCrash, ShieldAlert, CalendarX, Plus, Trash2, Pencil, ExternalLink, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { createIncident } from "@/lib/api";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { SLOModal } from "@/components/reliability/SLOModal";
import { UptimeStrip } from "@/components/reliability/UptimeStrip";
import { UptimeBarChart } from "@/components/reliability/UptimeBarChart";
import { ResponseTimeChart } from "@/components/reliability/ResponseTimeChart";
import {
  getSLATarget,
  getSLATargetUptime,
  listSLOs,
  getSLOStatus,
  deleteSLO,
  probeSLATarget,
  getSLATargetResponseTime,
  getSLORecommendations,
} from "@/lib/api_reliability";
import {
  formatUptimePct,
  formatMtbf,
  formatDuration,
  STATUS_LABEL,
  statusColors,
  WINDOW_OPTIONS,
} from "@/lib/uptime";
import type {
  SLATargetResponse,
  SLATargetUptimeResponse,
  SLOResponse,
  SLOStatusResponse,
  ResponseTimeResponse,
  SLORecommendation,
} from "@/lib/types";
export default function TargetDetailPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-fg-muted">Loading target…</div>}>
      <TargetDetailContent />
    </Suspense>
  );
}

type Uptime = SLATargetUptimeResponse | null;

function formatLatency(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}

function UptimeSummaryCard({ label, uptime }: { label: string; uptime: Uptime }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{label}</p>
      <p className="mt-2 text-3xl font-light tracking-tight text-fg-primary">
        {formatUptimePct(uptime?.uptime_pct)}
      </p>
      <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-3 text-xs text-fg-secondary">
        <span className="uppercase tracking-wide text-fg-muted">MTBF</span>
        <span className="font-mono">{formatMtbf(uptime?.mtbf_seconds)}</span>
      </div>
    </div>
  );
}

function TargetDetailContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") || "";
  const navigateDashboard = useDashboardNavigation();
  const toast = useToast();

  const [target, setTarget] = useState<SLATargetResponse | null>(null);
  const [last24, setLast24] = useState<Uptime>(null);
  const [d7, setD7] = useState<Uptime>(null);
  const [d30, setD30] = useState<Uptime>(null);
  const [d365, setD365] = useState<Uptime>(null);
  const [historyWindow, setHistoryWindow] = useState("30d");
  const [history, setHistory] = useState<Uptime>(null);
  const [responseWindow, setResponseWindow] = useState("24h");
  const [responseTime, setResponseTime] = useState<ResponseTimeResponse | null>(null);
  const [responseHistoryWindow, setResponseHistoryWindow] = useState("30d");
  const [responseHistory, setResponseHistory] = useState<ResponseTimeResponse | null>(null);
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [customUptime, setCustomUptime] = useState<Uptime>(null);
  const [slos, setSlos] = useState<(SLOResponse & { status: SLOStatusResponse | null })[]>([]);
  const [recommendations, setRecommendations] = useState<SLORecommendation[]>([]);
  const [creatingFor, setCreatingFor] = useState<string | null>(null);
  const [confirmRec, setConfirmRec] = useState<SLORecommendation | null>(null);
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<string | null>(null);
  const [showSLOModal, setShowSLOModal] = useState(false);
  const [editingSLO, setEditingSLO] = useState<SLOResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [targetData, u24, u7, u30, u365, allSlos, recs] = await Promise.all([
        getSLATarget(id),
        getSLATargetUptime(id, "24h").catch(() => null),
        getSLATargetUptime(id, "7d").catch(() => null),
        getSLATargetUptime(id, "30d").catch(() => null),
        getSLATargetUptime(id, "365d").catch(() => null),
        listSLOs().catch(() => ({ items: [] })),
        getSLORecommendations().catch(() => null),
      ]);

      const targetSlos = allSlos.items.filter((s) => s.target_id === id);
      const slosWithStatus = await Promise.all(
        targetSlos.map(async (slo) => ({
          ...slo,
          status: await getSLOStatus(slo.id).catch(() => null),
        })),
      );

      setTarget(targetData);
      setLast24(u24);
      setD7(u7);
      setD30(u30);
      setD365(u365);
      setSlos(slosWithStatus);
      setRecommendations((recs?.items ?? []).filter((r) => r.target_id === id));
    } catch (err) {
      console.error("Failed to load target details:", err);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Uptime history strip follows the selected window.
  useEffect(() => {
    if (!id) return;
    getSLATargetUptime(id, historyWindow).then(setHistory).catch(() => setHistory(null));
  }, [id, historyWindow]);

  useEffect(() => {
    if (!id) return;
    getSLATargetResponseTime(id, responseWindow)
      .then(setResponseTime)
      .catch(() => setResponseTime(null));
  }, [id, responseWindow]);

  useEffect(() => {
    if (!id) return;
    getSLATargetResponseTime(id, responseHistoryWindow)
      .then(setResponseHistory)
      .catch(() => setResponseHistory(null));
  }, [id, responseHistoryWindow]);

  async function handleProbe() {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await probeSLATarget(id);
      setProbeResult(r.up ? `Up${r.latency_ms != null ? ` · ${r.latency_ms}ms` : ""}` : "Down");
      await load();
    } catch {
      setProbeResult("Check failed");
    } finally {
      setProbing(false);
    }
  }

  async function runCustomRange() {
    if (!customStart || !customEnd) return;
    const start = new Date(customStart).toISOString();
    const end = new Date(customEnd).toISOString();
    try {
      setCustomUptime(await getSLATargetUptime(id, "30d", { start, end }));
    } catch {
      setCustomUptime(null);
    }
  }

  // Open a P0 (critical → P0) incident in the SLO's owning service straight
  // from the recommendation pill, then jump to the new incident.
  async function createIncidentFromRec(rec: SLORecommendation) {
    const serviceId = rec.service_id ?? target?.service_id ?? undefined;
    setCreatingFor(rec.slo_id);
    try {
      const result = await createIncident({
        title: `SLO breach: ${rec.slo_name}`,
        description:
          `${rec.target_name} SLO "${rec.slo_name}" is breaching its ` +
          `${formatUptimePct(rec.objective_pct)} objective ` +
          `(actual ${formatUptimePct(rec.actual_pct)}). ` +
          `Opened from the Reliability detail page.`,
        severity: "critical",
        service_id: serviceId,
      });
      toast.success("P0 incident created.", {
        label: "Open incident",
        href: `/dashboard/incidents/detail?id=${result.id}`,
      });
      setConfirmRec(null);
      navigateDashboard(`/dashboard/incidents/detail?id=${result.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setCreatingFor(null);
    }
  }

  const handleDeleteSLO = async (sloId: string) => {
    if (!confirm("Delete this SLO?")) return;
    try {
      await deleteSLO(sloId);
      load();
    } catch (err) {
      console.error("Failed to delete SLO", err);
    }
  };

  if (!loading && !target) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center">
        <ServerCrash className="mb-4 h-10 w-10 text-fg-muted" />
        <h2 className="text-lg font-semibold text-fg-primary">Target Not Found</h2>
        <Link href="/dashboard/reliability" className="mt-4 text-sm text-accent hover:underline">
          Return to Reliability
        </Link>
      </div>
    );
  }

  const colors = statusColors(target?.current_status ?? "unknown");
  const down24 = last24?.down_events ?? 0;

  return (
    <div className="flex h-full max-h-screen flex-col overflow-y-auto">
      {/* Header */}
      <header className="flex shrink-0 flex-col gap-2 border-b border-border-subtle px-6 py-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigateDashboard("/dashboard/reliability")}
            className="flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg-primary"
          >
            <ArrowLeft size={18} />
          </button>
          {loading ? (
            <div className="h-5 w-40 animate-pulse rounded bg-bg-elevated" />
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <h1 className="truncate text-base font-semibold text-fg-primary">{target?.name}</h1>
              <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${colors.bg} ${colors.text}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${colors.dot}`} />
                {STATUS_LABEL[target?.current_status ?? "unknown"]}
              </span>
              <Badge variant="default" className="shrink-0 text-[10px] uppercase">
                {target?.monitor_type ?? target?.kind}
              </Badge>
            </div>
          )}
          <Button variant="secondary" size="sm" onClick={handleProbe} loading={probing}>
            <RefreshCw size={13} /> Test check
          </Button>
        </div>
        {target?.url && (
          <a
            href={target.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-11 inline-flex max-w-full items-center gap-1.5 truncate font-mono text-xs text-fg-secondary hover:text-accent"
            title={target.url}
            onClick={(e) => e.stopPropagation()}
          >
            <span className="truncate">{target.url}</span>
            <ExternalLink size={12} className="shrink-0" />
          </a>
        )}
        {probeResult && <span className="ml-11 text-xs text-fg-muted">Last manual check: {probeResult}</span>}
      </header>

      <main className="flex-1 space-y-6 p-6">
        {/* Brief SLO warning + recommendation pill (advisory only) */}
        {!loading && recommendations.length > 0
          ? recommendations.map((rec) => {
              const critical = rec.severity === "critical";
              return (
                <div
                  key={rec.slo_id}
                  className={`flex flex-wrap items-center gap-x-2 gap-y-1 rounded-full border px-4 py-2 text-sm ${
                    critical
                      ? "border-status-critical-border bg-status-critical-bg/40"
                      : "border-status-warning-border bg-status-warning-bg/40"
                  }`}
                >
                  <ShieldAlert
                    size={14}
                    className={`shrink-0 ${critical ? "text-status-critical" : "text-status-warning"}`}
                  />
                  <span className={`font-semibold ${critical ? "text-status-critical" : "text-status-warning"}`}>
                    {critical ? "SLO breaching" : "SLO at risk"}
                  </span>
                  <span className="text-fg-secondary">
                    {rec.slo_name}: {formatUptimePct(rec.actual_pct)} vs{" "}
                    {formatUptimePct(rec.objective_pct)} objective.
                  </span>
                  {rec.service_id || target?.service_id ? (
                    <span className="text-fg-secondary">
                      Click{" "}
                      <button
                        type="button"
                        onClick={() => setConfirmRec(rec)}
                        disabled={creatingFor !== null}
                        className="font-medium text-accent underline hover:no-underline disabled:opacity-50 disabled:no-underline"
                      >
                        here
                      </button>{" "}
                      to create a P0 incident.
                    </span>
                  ) : null}
                </div>
              );
            })
          : null}

        {/* Top status cards */}
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Current Status</p>
            <p className={`mt-2 text-2xl font-semibold ${colors.text}`}>
              {STATUS_LABEL[target?.current_status ?? "unknown"]}
            </p>
            <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-fg-secondary">
              Monitoring {target?.is_active ? "enabled" : "paused"}
            </p>
          </div>

          <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Last Check</p>
            <p className="mt-2 text-2xl font-semibold text-fg-primary">
              {target?.last_check_at ? new Date(target.last_check_at).toLocaleTimeString() : "—"}
            </p>
            <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-fg-secondary">
              {target?.last_check_at ? new Date(target.last_check_at).toLocaleDateString() : "No checks recorded yet"}
            </p>
          </div>

          <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">Last 24 Hours</p>
              <span className="text-sm font-semibold text-fg-primary">{formatUptimePct(last24?.uptime_pct)}</span>
            </div>
            <div className="mt-3">
              <UptimeStrip series={last24?.series ?? []} height={28} />
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-3 text-xs text-fg-secondary">
              <span>{down24} down event{down24 === 1 ? "" : "s"}</span>
              <span>{formatDuration(last24?.downtime_seconds)} down</span>
            </div>
          </div>
        </div>

        {/* Uptime summary panels */}
        <div>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-fg-secondary">Uptime Summary</h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <UptimeSummaryCard label="Last 7 Days" uptime={d7} />
            <UptimeSummaryCard label="Last 30 Days" uptime={d30} />
            <UptimeSummaryCard label="Last 365 Days" uptime={d365} />
          </div>

          {/* Custom range */}
          <div className="mt-4 rounded-xl border border-border-subtle bg-bg-panel p-4 shadow-sm">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-fg-muted">From</label>
                <input
                  type="datetime-local"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  className="h-9 rounded-md border border-border-strong bg-bg-input px-2 text-sm text-fg-primary"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-fg-muted">To</label>
                <input
                  type="datetime-local"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  className="h-9 rounded-md border border-border-strong bg-bg-input px-2 text-sm text-fg-primary"
                />
              </div>
              <Button variant="secondary" size="sm" onClick={runCustomRange} disabled={!customStart || !customEnd}>
                Calculate
              </Button>
              {customUptime && (
                <div className="ml-auto flex items-center gap-4 text-sm">
                  <span><span className="text-fg-muted">Uptime </span><span className="font-semibold text-fg-primary">{formatUptimePct(customUptime.uptime_pct)}</span></span>
                  <span><span className="text-fg-muted">MTBF </span><span className="font-mono text-fg-primary">{formatMtbf(customUptime.mtbf_seconds)}</span></span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Uptime history */}
        <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-medium uppercase tracking-wide text-fg-secondary">Uptime History</h2>
            <div className="flex items-center gap-1">
              {WINDOW_OPTIONS.filter((w) => w.value !== "90d").map((w) => (
                <button
                  key={w.value}
                  onClick={() => setHistoryWindow(w.value)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                    historyWindow === w.value
                      ? "border border-border-strong bg-bg-elevated text-fg-primary"
                      : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
                  }`}
                >
                  {w.value}
                </button>
              ))}
            </div>
          </div>
          <UptimeBarChart series={history?.series ?? []} windowValue={historyWindow} />
          <div className="mt-3 flex items-center justify-between text-xs text-fg-muted">
            <span className="inline-flex items-center gap-3">
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-status-success" /> Up</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-status-critical" /> Down</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-border-subtle" /> No data</span>
            </span>
            <span>{formatUptimePct(history?.uptime_pct)} over {historyWindow}</span>
          </div>
        </div>

        {/* Response time */}
        <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium uppercase tracking-wide text-fg-secondary">Response Time</h2>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
                <span>Avg <strong className="text-fg-primary">{formatLatency(responseTime?.avg_latency_ms)}</strong></span>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {["15m", "30m", "1h", "6h", "12h", "24h"].map((window) => (
                <button
                  key={window}
                  onClick={() => setResponseWindow(window)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                    responseWindow === window
                      ? "border border-border-strong bg-bg-elevated text-fg-primary"
                      : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
                  }`}
                >
                  {window}
                </button>
              ))}
            </div>
          </div>
          <ResponseTimeChart
            series={responseTime?.series ?? []}
            windowValue={responseWindow}
          />
          <div className="mt-2 flex items-center justify-between text-xs text-fg-muted">
            <span><span className="text-accent">Line</span> average response time</span>
            <span>{responseTime?.total_samples ?? 0} samples</span>
          </div>
        </div>

        {/* Response time history */}
        <div className="rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium uppercase tracking-wide text-fg-secondary">Response Time History</h2>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
                <span>Avg <strong className="text-fg-primary">{formatLatency(responseHistory?.avg_latency_ms)}</strong></span>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {["7d", "30d", "90d", "365d"].map((window) => (
                <button
                  key={window}
                  onClick={() => setResponseHistoryWindow(window)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                    responseHistoryWindow === window
                      ? "border border-border-strong bg-bg-elevated text-fg-primary"
                      : "text-fg-secondary hover:bg-bg-hover hover:text-fg-primary"
                  }`}
                >
                  {window}
                </button>
              ))}
            </div>
          </div>
          <ResponseTimeChart
            series={responseHistory?.series ?? []}
            windowValue={responseHistoryWindow}
          />
          <div className="mt-2 flex items-center justify-between text-xs text-fg-muted">
            <span><span className="text-accent">Line</span> average response time</span>
            <span>{responseHistory?.total_samples ?? 0} samples</span>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* SLOs (warning-only) */}
          <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-medium uppercase tracking-wide text-fg-secondary">SLO Warnings</h2>
              <Button variant="secondary" size="sm" onClick={() => { setEditingSLO(null); setShowSLOModal(true); }}>
                <Plus size={14} /> New SLO
              </Button>
            </div>
            <div className="flex-1 space-y-3">
              {slos.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center py-6 text-center text-fg-muted">
                  <ShieldAlert className="mb-2 h-8 w-8" />
                  <p className="text-sm">No SLOs defined</p>
                </div>
              ) : (
                slos.map((slo) => {
                  const breached = slo.status ? !slo.status.compliant : false;
                  return (
                    <div key={slo.id} className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
                      <div className="mb-3 flex items-center justify-between">
                        <span className="text-sm font-medium text-fg-primary">{slo.name}</span>
                        <div className="flex items-center gap-2">
                          {breached ? (
                            <Badge className="gap-1 border-status-warning-border bg-status-warning-bg text-status-warning">
                              <ShieldAlert size={12} /> Warning
                            </Badge>
                          ) : (
                            <Badge className="border-status-success-border bg-status-success-bg text-status-success">OK</Badge>
                          )}
                          <button onClick={() => { setEditingSLO(slo); setShowSLOModal(true); }} className="text-fg-muted hover:text-fg-primary" title="Edit">
                            <Pencil size={14} />
                          </button>
                          <button onClick={() => handleDeleteSLO(slo.id)} className="text-fg-muted hover:text-status-critical" title="Delete">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                      <div className="mb-1.5 flex items-center justify-between text-xs text-fg-secondary">
                        <span>Target: {formatUptimePct(slo.objective_pct)}</span>
                        <span>Actual: {formatUptimePct(slo.status?.actual_pct)}</span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-border-subtle">
                        <div
                          className={`h-full ${breached ? "bg-status-warning" : "bg-status-success"}`}
                          style={{ width: `${Math.min(100, Math.max(0, slo.status?.actual_pct ?? 0))}%` }}
                        />
                      </div>
                      <p className="mt-2 text-[11px] text-fg-muted">
                        Over {Math.round(slo.window_seconds / 86400)} days
                      </p>
                    </div>
                  );
                })
              )}
            </div>
            <p className="mt-3 border-t border-border-subtle pt-3 text-[11px] text-fg-muted">
              SLO warnings are shown here. OpsMender does not create incidents from SLO breaches yet.
            </p>
          </div>

          {/* Outage history */}
          <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-medium uppercase tracking-wide text-fg-secondary">Outage History</h2>
              <span className="text-[11px] text-fg-muted">over {historyWindow}</span>
            </div>
            <div className="flex-1">
              {(history?.episodes?.length ?? 0) === 0 ? (
                <div className="flex h-full flex-col items-center justify-center py-8 text-center text-fg-muted">
                  <ShieldAlert className="mb-2 h-8 w-8" />
                  <p className="text-sm">No outages in this window</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border-subtle">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated text-left text-[11px] font-medium uppercase tracking-wide text-fg-secondary">
                        <th className="px-3 py-2">Started</th>
                        <th className="px-3 py-2">Ended</th>
                        <th className="px-3 py-2 text-right">Duration</th>
                        <th className="px-3 py-2 text-right">Type</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {(history?.episodes ?? []).map((ep, i) => (
                        <tr
                          key={`${ep.started_at}-${i}`}
                          className={ep.maintenance ? "bg-status-info-bg/40" : ""}
                        >
                          <td className="px-3 py-2 text-fg-primary">
                            {new Date(ep.started_at).toLocaleString()}
                          </td>
                          <td className="px-3 py-2 text-fg-secondary">
                            {ep.ended_at ? (
                              new Date(ep.ended_at).toLocaleString()
                            ) : (
                              <span className="text-status-critical">Ongoing</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-fg-primary">
                            {formatDuration(ep.duration_seconds)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            {ep.maintenance ? (
                              <Badge variant="info" className="gap-1 text-[10px]">
                                <CalendarX size={11} /> Maintenance
                              </Badge>
                            ) : (
                              <Badge
                                variant="critical"
                                className="text-[10px]"
                              >
                                Outage
                              </Badge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <p className="mt-3 border-t border-border-subtle pt-3 text-[11px] text-fg-muted">
              Outages are derived from uptime checks. Maintenance-window outages
              are shown separately and excluded from SLA/SLO calculations.
            </p>
          </div>
        </div>

        <p className="text-center text-[11px] text-fg-muted">
          Response time charts, TCP checks, and health-check endpoint monitoring are planned future enhancements.
        </p>
      </main>

      <SLOModal
        open={showSLOModal}
        onClose={() => setShowSLOModal(false)}
        onSaved={load}
        targetId={id}
        initialData={editingSLO}
      />

      <Modal
        open={confirmRec !== null}
        onClose={() => setConfirmRec(null)}
        title="Create P0 incident?"
      >
        <p className="text-sm text-fg-secondary">
          Are you sure you want to create a P0 incident
          {confirmRec?.service_name ? (
            <> for <span className="font-medium text-fg-primary">{confirmRec.service_name}</span></>
          ) : null}
          ? P0 is the highest priority and will page the owning on-call.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmRec(null)} disabled={creatingFor !== null}>
            Cancel
          </Button>
          <Button
            variant="danger"
            loading={creatingFor !== null}
            onClick={() => {
              if (confirmRec) void createIncidentFromRec(confirmRec);
            }}
          >
            Create P0 incident
          </Button>
        </div>
      </Modal>
    </div>
  );
}
