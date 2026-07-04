"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Plus, ServerCrash, Calendar, Trash2, Pencil, Shield } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { MaintenanceWindowModal } from "@/components/reliability/MaintenanceWindowModal";
import { SLATargetModal } from "@/components/reliability/SLATargetModal";
import {
  listSLATargets,
  getSLASummary,
  listMaintenanceWindows,
  deleteMaintenanceWindow,
  deleteSLATarget,
  getSLORecommendations,
} from "@/lib/api_reliability";
import { formatUptimePct, STATUS_LABEL, statusColors } from "@/lib/uptime";
import { formatDate, formatDateTime } from "@/lib/formatDate";
import type {
  SLATargetResponse,
  SLASummaryResponse,
  MaintenanceWindowResponse,
  SLORecommendation,
} from "@/lib/types";

function fmtLastCheck(iso: string | null): string {
  if (!iso) return "Never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return formatDate(iso);
}

function SummaryStat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">{label}</p>
      <p className={`mt-1.5 text-2xl font-semibold tracking-tight ${tone ?? "text-fg-primary"}`}>{value}</p>
    </div>
  );
}

export default function ReliabilityPage() {
  const [targets, setTargets] = useState<SLATargetResponse[]>([]);
  const [summary, setSummary] = useState<SLASummaryResponse | null>(null);
  const [maintenanceWindows, setMaintenanceWindows] = useState<MaintenanceWindowResponse[]>([]);
  const [recommendations, setRecommendations] = useState<SLORecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showMWModal, setShowMWModal] = useState(false);
  const [editingMW, setEditingMW] = useState<MaintenanceWindowResponse | null>(null);
  const [showTargetModal, setShowTargetModal] = useState(false);
  const [editingTarget, setEditingTarget] = useState<SLATargetResponse | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [targetsData, summaryData, mwData, recsData] = await Promise.all([
        listSLATargets(),
        getSLASummary().catch(() => null),
        listMaintenanceWindows(),
        getSLORecommendations().catch(() => null),
      ]);
      setTargets(targetsData.items);
      setSummary(summaryData);
      setMaintenanceWindows(mwData.items);
      setRecommendations(recsData?.items ?? []);
    } catch (err) {
      console.error("Failed to load reliability data:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteMW = async (id: string) => {
    if (!confirm("Delete this maintenance window?")) return;
    try {
      await deleteMaintenanceWindow(id);
      loadData();
    } catch (err) {
      console.error("Failed to delete maintenance window", err);
    }
  };

  const handleDeleteTarget = async (id: string) => {
    if (!confirm("Delete this monitored target? All associated SLOs will also be deleted.")) return;
    try {
      await deleteSLATarget(id);
      loadData();
    } catch (err) {
      console.error("Failed to delete target", err);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div className="flex flex-col h-full max-h-screen overflow-y-auto">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border-subtle px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-accent-bg text-accent-text">
            <Activity size={18} />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-fg-primary">Reliability &amp; SLA</h1>
            <p className="text-xs text-fg-muted">Monitor HTTP/HTTPS uptime and SLA compliance for your services.</p>
          </div>
        </div>
        <Button onClick={() => { setEditingTarget(null); setShowTargetModal(true); }}>
          <Plus size={14} /> New Target
        </Button>
      </header>

      <main className="flex-1 p-6">
        <div className="space-y-6">
          {/* Summary row */}
          {summary && summary.total_targets > 0 && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <SummaryStat label="Targets" value={String(summary.total_targets)} />
              <SummaryStat label="Up" value={String(summary.targets_up)} tone="text-status-success" />
              <SummaryStat label="Down" value={String(summary.targets_down)} tone={summary.targets_down > 0 ? "text-status-critical" : undefined} />
              <SummaryStat label="Avg 30d uptime" value={formatUptimePct(summary.avg_uptime_30d_pct)} />
              <SummaryStat
                label="SLO warnings"
                value={String(summary.active_slo_warnings)}
                tone={summary.active_slo_warnings > 0 ? "text-status-warning" : undefined}
              />
            </div>
          )}

          <div>
            <h2 className="text-base font-semibold text-fg-primary">Monitored Targets</h2>
            <p className="text-sm text-fg-secondary">HTTP/HTTPS endpoints OpsMender checks for availability.</p>
          </div>

          {loading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-44 rounded-xl border border-border-subtle bg-bg-elevated animate-pulse" />
              ))}
            </div>
          ) : targets.length === 0 ? (
            <EmptyState
              icon={ServerCrash}
              title="No targets configured"
              description="Add your first HTTP/HTTPS endpoint to start monitoring uptime and SLA compliance."
              learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
              learnMoreLabel="Operator guide"
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {targets.map((target) => {
                const colors = statusColors(target.current_status);
                // A target is "red" (SLO not satisfied) when it has any
                // breaching / at-risk SLO recommendation. Green when it has
                // SLOs and none are flagged; neutral when no SLOs are defined.
                // The recommendation detail itself now lives on the target's
                // detail page as a brief pill.
                const breaching = recommendations.some(
                  (rec) => rec.target_id === target.id,
                );
                const uptimeColor = breaching
                  ? "text-status-critical"
                  : target.active_slo_count > 0
                    ? "text-status-success"
                    : "text-fg-primary";
                return (
                  <Link
                    key={target.id}
                    href={`/dashboard/reliability/detail?id=${target.id}`}
                    className="group relative flex flex-col gap-3 overflow-hidden rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm transition-all hover:border-border-strong hover:shadow-md"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="truncate font-medium text-fg-primary">{target.name}</h3>
                          <Badge variant="default" className="shrink-0 text-[10px] uppercase">
                            {target.monitor_type ?? target.kind}
                          </Badge>
                        </div>
                        {target.url ? (
                          <p className="mt-1 truncate font-mono text-[11px] text-fg-muted" title={target.url}>
                            {target.url}
                          </p>
                        ) : (
                          <p className="mt-1 text-[11px] italic text-fg-muted">No URL configured</p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setEditingTarget(target); setShowTargetModal(true); }}
                          className="p-1 text-fg-muted hover:text-fg-primary"
                          title="Edit target"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteTarget(target.id); }}
                          className="p-1 text-fg-muted hover:text-status-critical"
                          title="Delete target"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    <div className="flex items-end justify-between gap-2">
                      <div>
                        <span className={`text-3xl font-light tracking-tight ${uptimeColor}`}>
                          {formatUptimePct(target.uptime_30d_pct)}
                        </span>
                        <span className="ml-1.5 text-xs text-fg-secondary">30d uptime</span>
                        {/* Explain the red: without this, a red uptime next to
                            an "Up" status pill reads as a mixed signal. */}
                        {breaching && (
                          <p className="mt-0.5 text-[11px] font-medium text-status-critical">
                            Below SLO target
                          </p>
                        )}
                      </div>
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ${colors.bg} ${colors.text}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${colors.dot}`} />
                        {STATUS_LABEL[target.current_status]}
                      </span>
                    </div>

                    <div className="flex items-center justify-between border-t border-border-subtle pt-3 text-xs text-fg-muted">
                      <span>Last check: {fmtLastCheck(target.last_check_at)}</span>
                      <span className="inline-flex items-center gap-1">
                        <Shield size={12} /> {target.active_slo_count} SLO{target.active_slo_count === 1 ? "" : "s"}
                      </span>
                    </div>
                    {target.service_name ? (
                      <p className="-mt-1 truncate text-[11px] text-fg-muted" title={target.service_name}>
                        Service: <span className="text-fg-secondary">{target.service_name}</span>
                        {target.team_name ? ` · ${target.team_name}` : ""}
                      </p>
                    ) : null}
                  </Link>
                );
              })}
            </div>
          )}

          {/* Maintenance Windows */}
          <div className="border-t border-border-subtle pt-8">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold text-fg-primary">Maintenance Windows</h2>
                <p className="text-sm text-fg-secondary">
                  Planned downtime where uptime alerts are suppressed and the time is excluded from SLA calculations.
                </p>
              </div>
              <Button size="sm" onClick={() => { setEditingMW(null); setShowMWModal(true); }}>
                <Plus size={14} /> Schedule Maintenance
              </Button>
            </div>

            {loading ? (
              <div className="h-40 rounded-xl border border-border-subtle bg-bg-elevated animate-pulse" />
            ) : maintenanceWindows.length === 0 ? (
              <EmptyState
                icon={Calendar}
                title="No maintenance windows"
                description="Schedule maintenance to suppress alerts and exclude that time from SLA calculations."
                learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
                learnMoreLabel="Operator guide"
              />
            ) : (
              <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium uppercase tracking-wide text-fg-secondary">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Targets</th>
                      <th className="px-4 py-3">Schedule</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {maintenanceWindows.map((mw) => {
                      const start = new Date(mw.starts_at);
                      const end = new Date(mw.ends_at);
                      const now = new Date();
                      const isActive = start <= now && end >= now;
                      return (
                        <tr key={mw.id} className="transition-colors hover:bg-bg-elevated">
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-fg-primary">{mw.name}</span>
                              {isActive && (
                                <Badge className="border-status-warning-border bg-status-warning-bg text-status-warning">Active</Badge>
                              )}
                            </div>
                            {mw.reason && <p className="mt-0.5 max-w-md truncate text-xs text-fg-muted">{mw.reason}</p>}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-1">
                              {mw.target_ids.includes("*") ? (
                                <Badge variant="default" className="text-[10px]">All Targets</Badge>
                              ) : (
                                mw.target_ids.map((id) => {
                                  const t = targets.find((t) => t.id === id);
                                  return (
                                    <Badge key={id} variant="default" className="text-[10px]">
                                      {t?.name || id.slice(0, 8)}
                                    </Badge>
                                  );
                                })
                              )}
                            </div>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3">
                            <p className="text-sm text-fg-primary">{formatDateTime(start)} - {formatDateTime(end)}</p>
                            {mw.rrule && <p className="mt-0.5 font-mono text-xs text-fg-muted">Repeats: {mw.rrule}</p>}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <button onClick={() => { setEditingMW(mw); setShowMWModal(true); }} className="p-1 text-fg-muted hover:text-fg-primary" title="Edit">
                                <Pencil size={14} />
                              </button>
                              <button onClick={() => handleDeleteMW(mw.id)} className="p-1 text-fg-muted hover:text-status-critical" title="Delete">
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </main>

      <MaintenanceWindowModal
        open={showMWModal}
        onClose={() => setShowMWModal(false)}
        onSaved={loadData}
        targets={targets}
        initialData={editingMW}
      />

      <SLATargetModal
        open={showTargetModal}
        onClose={() => setShowTargetModal(false)}
        onSaved={loadData}
        initialData={editingTarget}
      />
    </div>
  );
}
