"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Activity, ArrowLeft, Clock, ServerCrash, ShieldAlert, ShieldCheck, Zap, Plus, Trash2, Pencil } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SLOModal } from "@/components/reliability/SLOModal";
import {
  getSLATarget,
  getSLATargetUptime,
  listSLOs,
  getSLOStatus,
  getSLATargetIncidents,
  deleteSLO,
} from "@/lib/api_reliability";
import type {
  SLATargetResponse,
  SLATargetUptimeResponse,
  SLOResponse,
  SLOStatusResponse,
  IncidentResponse,
} from "@/lib/types";

export default function TargetDetailPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-fg-muted">Loading target...</div>}>
      <TargetDetailContent />
    </Suspense>
  );
}

function TargetDetailContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") || "";
  const router = useRouter();

  const [target, setTarget] = useState<SLATargetResponse | null>(null);
  const [window, setWindow] = useState<string>("30d");
  const [uptime, setUptime] = useState<SLATargetUptimeResponse | null>(null);
  const [incidents, setIncidents] = useState<IncidentResponse[]>([]);
  const [slos, setSlos] = useState<(SLOResponse & { status: SLOStatusResponse | null })[]>([]);
  const [loading, setLoading] = useState(true);
  const [showSLOModal, setShowSLOModal] = useState(false);
  const [editingSLO, setEditingSLO] = useState<SLOResponse | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [targetData, uptimeData, incData, allSlos] = await Promise.all([
        getSLATarget(id),
        getSLATargetUptime(id, window).catch(() => null),
        getSLATargetIncidents(id).catch(() => []),
        listSLOs().catch(() => ({ items: [] })),
      ]);

      const targetSlos = allSlos.items.filter((s) => s.target_id === id);
      const slosWithStatus = await Promise.all(
        targetSlos.map(async (slo) => {
          const status = await getSLOStatus(slo.id).catch(() => null);
          return { ...slo, status };
        })
      );

      setTarget(targetData);
      setUptime(uptimeData);
      setIncidents(incData);
      setSlos(slosWithStatus);
    } catch (err) {
      console.error("Failed to load target details:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSLO = async (sloId: string) => {
    if (!confirm("Are you sure you want to delete this SLO?")) return;
    try {
      await deleteSLO(sloId);
      load();
    } catch (err) {
      console.error("Failed to delete SLO", err);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, window]);

  if (!loading && !target) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center">
        <ServerCrash className="h-10 w-10 text-fg-muted mb-4" />
        <h2 className="text-lg font-semibold text-fg-primary">Target Not Found</h2>
        <p className="text-sm text-fg-secondary mt-1">This SLA target does not exist or was deleted.</p>
        <Link href="/dashboard/reliability" className="mt-4 text-sm text-accent hover:underline">
          Return to Reliability
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full max-h-screen overflow-y-auto">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border-subtle px-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/dashboard/reliability")}
            className="flex h-8 w-8 items-center justify-center rounded-md text-fg-muted hover:bg-bg-hover hover:text-fg-primary transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          {loading ? (
            <div className="h-5 w-40 animate-pulse rounded bg-bg-elevated" />
          ) : (
            <div className="flex items-center gap-3">
              <h1 className="text-sm font-semibold text-fg-primary">{target?.name}</h1>
              <Badge variant="default" className="text-[10px] uppercase">
                {target?.kind}
              </Badge>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {["7d", "30d", "90d", "1y"].map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                window === w
                  ? "bg-bg-elevated text-fg-primary border border-border-strong"
                  : "text-fg-secondary hover:text-fg-primary hover:bg-bg-hover"
              }`}
            >
              {w}
            </button>
          ))}
        </div>
      </header>

      <main className="flex-1 p-6">
        <div className="max-w-6xl mx-auto space-y-6">
          {loading ? (
            <div className="space-y-6">
              <div className="h-40 rounded-xl bg-bg-elevated animate-pulse border border-border-subtle" />
              <div className="grid gap-4 md:grid-cols-2">
                <div className="h-60 rounded-xl bg-bg-elevated animate-pulse border border-border-subtle" />
                <div className="h-60 rounded-xl bg-bg-elevated animate-pulse border border-border-subtle" />
              </div>
            </div>
          ) : (
            <>
              {/* Uptime Overview */}
              <div className="rounded-xl border border-border-subtle bg-bg-panel p-6 shadow-sm">
                <h2 className="text-sm font-medium text-fg-secondary mb-4 uppercase tracking-wider">
                  Availability Overview ({window})
                </h2>
                <div className="flex items-end gap-3">
                  <span className="text-5xl font-light tracking-tight text-fg-primary">
                    {uptime ? uptime.uptime_pct.toFixed(2) : "100.00"}%
                  </span>
                  <span className="text-sm text-fg-secondary mb-2 border-l border-border-subtle pl-3">
                    {uptime?.total_samples || 0} samples
                  </span>
                </div>
                
                <div className="mt-8 grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 border-t border-border-subtle">
                  <div>
                    <p className="text-xs text-fg-muted mb-1">Up Samples</p>
                    <p className="font-mono text-sm">{uptime?.up_samples || 0}</p>
                  </div>
                  <div>
                    <p className="text-xs text-fg-muted mb-1">Down Samples</p>
                    <p className="font-mono text-sm">{(uptime?.total_samples || 0) - (uptime?.up_samples || 0)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-fg-muted mb-1">Downtime</p>
                    <p className="font-mono text-sm">{(uptime?.downtime_seconds || 0) / 60} min</p>
                  </div>
                  <div>
                    <p className="text-xs text-fg-muted mb-1">Suppressed</p>
                    <p className="font-mono text-sm">{(uptime?.suppressed_seconds || 0) / 60} min</p>
                  </div>
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                {/* SLOs */}
                <div className="rounded-xl border border-border-subtle bg-bg-panel p-6 shadow-sm flex flex-col">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-sm font-medium text-fg-secondary uppercase tracking-wider">
                      Service Level Objectives
                    </h2>
                    <Button variant="secondary" size="sm" onClick={() => { setEditingSLO(null); setShowSLOModal(true); }}>
                      <Plus size={14} /> New SLO
                    </Button>
                  </div>
                  <div className="flex-1 space-y-4">
                    {slos.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-full text-center text-fg-muted">
                        <ShieldAlert className="h-8 w-8 mb-2" />
                        <p className="text-sm">No SLOs defined</p>
                      </div>
                    ) : (
                      slos.map((slo) => (
                        <div key={slo.id} className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
                          <div className="flex items-center justify-between mb-3">
                            <span className="font-medium text-sm text-fg-primary">{slo.name}</span>
                            <div className="flex items-center gap-2">
                              {slo.status?.compliant ? (
                                <Badge className="bg-status-success-bg text-status-success border-status-success-border gap-1">
                                  <ShieldCheck size={12} /> Compliant
                                </Badge>
                              ) : (
                                <Badge className="bg-status-critical-bg text-status-critical border-status-critical-border gap-1">
                                  <Activity size={12} /> Violating
                                </Badge>
                              )}
                              <button onClick={() => { setEditingSLO(slo); setShowSLOModal(true); }} className="text-fg-muted hover:text-fg-primary" title="Edit">
                                <Pencil size={14} />
                              </button>
                              <button onClick={() => handleDeleteSLO(slo.id)} className="text-fg-muted hover:text-status-critical" title="Delete">
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </div>
                          
                          <div className="flex items-center justify-between text-xs text-fg-secondary mb-1.5">
                            <span>Target: {slo.objective_pct}%</span>
                            <span>Actual: {slo.status?.actual_pct.toFixed(2)}%</span>
                          </div>
                          <div className="h-1.5 w-full rounded-full bg-border-subtle overflow-hidden">
                            <div 
                              className={`h-full ${slo.status?.compliant ? 'bg-status-success' : 'bg-status-critical'}`} 
                              style={{ width: `${Math.min(100, Math.max(0, slo.status?.actual_pct || 0))}%` }} 
                            />
                          </div>
                          
                          <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                            <div className="bg-bg-panel rounded px-2 py-1.5 border border-border-subtle">
                              <span className="text-fg-muted block mb-0.5">Error Budget</span>
                              <span className={`font-mono ${slo.status && slo.status.error_budget_remaining_pct < 0 ? 'text-status-critical' : 'text-fg-primary'}`}>
                                {slo.status?.error_budget_remaining_pct.toFixed(1)}% remaining
                              </span>
                            </div>
                            <div className="bg-bg-panel rounded px-2 py-1.5 border border-border-subtle">
                              <span className="text-fg-muted block mb-0.5">Burn Rate</span>
                              <span className={`font-mono ${slo.status && slo.status.burn_rate > 1 ? 'text-status-warning' : 'text-fg-primary'}`}>
                                {slo.status?.burn_rate.toFixed(2)}x
                              </span>
                            </div>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* Incidents */}
                <div className="rounded-xl border border-border-subtle bg-bg-panel p-6 shadow-sm flex flex-col">
                  <h2 className="text-sm font-medium text-fg-secondary mb-4 uppercase tracking-wider">
                    Linked Incidents
                  </h2>
                  <div className="flex-1">
                    {incidents.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-full text-center text-fg-muted py-8">
                        <Zap className="h-8 w-8 mb-2" />
                        <p className="text-sm">No incidents linked to this target</p>
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {incidents.slice(0, 5).map((inc) => (
                          <Link 
                            key={inc.id}
                            href={`/dashboard/incidents/${inc.id}`}
                            className="block rounded-lg border border-border-subtle bg-bg-elevated p-3 hover:border-border-strong transition-colors"
                          >
                            <div className="flex items-start justify-between">
                              <h3 className="text-sm font-medium text-fg-primary line-clamp-1 flex-1 pr-2">
                                {inc.title}
                              </h3>
                              <Badge variant="default" className="text-[10px] shrink-0">
                                {inc.status}
                              </Badge>
                            </div>
                            <div className="mt-2 flex items-center justify-between text-xs text-fg-muted">
                              <span className="flex items-center gap-1"><Clock size={12}/> {new Date(inc.created_at).toLocaleString()}</span>
                              <span className="uppercase">{inc.external_source || "manual"}</span>
                            </div>
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      <SLOModal
        open={showSLOModal}
        onClose={() => setShowSLOModal(false)}
        onSaved={load}
        targetId={id}
        initialData={editingSLO}
      />
    </div>
  );
}
