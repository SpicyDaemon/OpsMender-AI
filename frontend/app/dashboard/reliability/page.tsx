"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Plus, ServerCrash, Shield, Clock, Calendar, Trash2, Pencil } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { MaintenanceWindowModal } from "@/components/reliability/MaintenanceWindowModal";
import { SLATargetModal } from "@/components/reliability/SLATargetModal";
import { SLOModal } from "@/components/reliability/SLOModal";
import {
  listSLATargets,
  getSLATargetUptime,
  listSLOs,
  getSLOStatus,
  listMaintenanceWindows,
  deleteMaintenanceWindow,
  deleteSLATarget,
  deleteSLO,
} from "@/lib/api_reliability";
import type {
  SLATargetResponse,
  SLOResponse,
  SLATargetUptimeResponse,
  SLOStatusResponse,
  MaintenanceWindowResponse,
} from "@/lib/types";

interface TargetWithMetrics extends SLATargetResponse {
  uptime30d: SLATargetUptimeResponse | null;
  uptime24h: SLATargetUptimeResponse | null;
  slos: (SLOResponse & { status: SLOStatusResponse | null })[];
  activeMaintenance: MaintenanceWindowResponse | null;
}

export default function ReliabilityPage() {
  const [targets, setTargets] = useState<TargetWithMetrics[]>([]);
  const [maintenanceWindows, setMaintenanceWindows] = useState<MaintenanceWindowResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showMWModal, setShowMWModal] = useState(false);
  const [editingMW, setEditingMW] = useState<MaintenanceWindowResponse | null>(null);
  const [showTargetModal, setShowTargetModal] = useState(false);
  const [editingTarget, setEditingTarget] = useState<SLATargetResponse | null>(null);
  const [showSLOModal, setShowSLOModal] = useState(false);
  const [editingSLO, setEditingSLO] = useState<SLOResponse | null>(null);
  const [activeTargetId, setActiveTargetId] = useState<string>("");

  const loadData = async () => {
    setLoading(true);
    try {
      const [targetsData, slosData, mwData] = await Promise.all([
          listSLATargets(),
          listSLOs(),
          listMaintenanceWindows(),
        ]);

        const slos = slosData.items;
        const now = new Date();
        const activeWindows = mwData.items.filter(w => {
          const start = new Date(w.starts_at);
          const end = new Date(w.ends_at);
          return start <= now && end >= now;
        });

        const targetMetrics: TargetWithMetrics[] = await Promise.all(
          targetsData.items.map(async (target) => {
            const uptime = await getSLATargetUptime(target.id, "30d").catch(() => null);
            const uptime24h = await getSLATargetUptime(target.id, "24h").catch(() => null);
            
            const targetSlos = slos.filter((s) => s.target_id === target.id);
            const slosWithStatus = await Promise.all(
              targetSlos.map(async (slo) => {
                const status = await getSLOStatus(slo.id).catch(() => null);
                return { ...slo, status };
              })
            );

            const activeMw = activeWindows.find(w => 
              w.target_ids.includes("*") || w.target_ids.includes(target.id)
            ) || null;

            return {
              ...target,
              uptime30d: uptime,
              uptime24h: uptime24h,
              slos: slosWithStatus,
              activeMaintenance: activeMw,
            };
          })
        );

        setTargets(targetMetrics);
        setMaintenanceWindows(mwData.items);
      } catch (err) {
        console.error("Failed to load reliability data:", err);
      } finally {
        setLoading(false);
      }
  };

  const handleDeleteMW = async (id: string) => {
    if (!confirm("Are you sure you want to delete this maintenance window?")) return;
    try {
      await deleteMaintenanceWindow(id);
      loadData();
    } catch (err) {
      console.error("Failed to delete maintenance window", err);
    }
  };

  const handleDeleteTarget = async (id: string) => {
    if (!confirm("Are you sure you want to delete this SLA Target? All associated SLOs will also be deleted.")) return;
    try {
      await deleteSLATarget(id);
      loadData();
    } catch (err) {
      console.error("Failed to delete SLA Target", err);
    }
  };

  const handleDeleteSLO = async (id: string) => {
    if (!confirm("Are you sure you want to delete this SLO?")) return;
    try {
      await deleteSLO(id);
      loadData();
    } catch (err) {
      console.error("Failed to delete SLO", err);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div className="flex flex-col h-full max-h-screen overflow-y-auto">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border-subtle px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-accent-bg text-accent">
            <Activity size={18} />
          </div>
          <h1 className="text-sm font-semibold text-fg-primary">Reliability & SLA</h1>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={() => { setEditingTarget(null); setShowTargetModal(true); }}>
            <Plus size={14} /> New Target
          </Button>
        </div>
      </header>

      <main className="flex-1 p-6">
        <div className="max-w-6xl mx-auto space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-fg-primary">Monitored Targets</h2>
              <p className="text-sm text-fg-secondary">Rolling 30-day uptime and SLO compliance.</p>
            </div>
          </div>

          {loading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-40 rounded-xl border border-border-subtle bg-bg-elevated animate-pulse" />
              ))}
            </div>
          ) : targets.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-strong px-6 py-16 text-center">
              <ServerCrash className="mb-3 h-8 w-8 text-fg-muted" />
              <p className="text-sm font-medium text-fg-primary">No SLA targets configured</p>
              <p className="mt-1 text-sm text-fg-secondary max-w-md">
                Configure your first HTTP or TCP target to begin monitoring uptime and tracking SLO error budgets.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {targets.map((target) => (
                <Link
                  key={target.id}
                  href={`/dashboard/reliability/detail?id=${target.id}`}
                  className="group relative flex flex-col justify-between overflow-hidden rounded-xl border border-border-subtle bg-bg-panel p-5 shadow-sm transition-all hover:border-border-strong hover:shadow-md"
                >
                  <div>
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium text-fg-primary">{target.name}</h3>
                        <Badge variant="default" className="text-[10px] uppercase">{target.kind}</Badge>
                      </div>
                      {target.activeMaintenance ? (
                        <div title="Maintenance Window Active" className="text-status-warning bg-status-warning-bg rounded-md p-1">
                          <Clock size={14} />
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          <button 
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setEditingTarget(target); setShowTargetModal(true); }}
                            className="text-fg-muted hover:text-fg-primary p-1"
                          >
                            <Pencil size={14} />
                          </button>
                          <button 
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteTarget(target.id); }}
                            className="text-fg-muted hover:text-status-critical p-1"
                          >
                            <Trash2 size={14} />
                          </button>
                          <div className={`h-2 w-2 rounded-full ${target.is_active ? "bg-status-success" : "bg-fg-muted"}`} />
                        </div>
                      )}
                    </div>
                    
                    <div className="mt-4 flex items-end justify-between gap-2">
                      <div className="flex items-end gap-2">
                        <span className="text-3xl font-light tracking-tight text-fg-primary">
                          {target.uptime30d ? target.uptime30d.uptime_pct.toFixed(2) : "100.00"}%
                        </span>
                        <span className="text-xs text-fg-secondary mb-1">30d uptime</span>
                      </div>
                      
                      {/* 24h Sparkline */}
                      {target.uptime24h?.series && target.uptime24h.series.length > 0 && (
                        <div className="h-8 w-24 flex items-end" title="24h uptime trend">
                          <svg className="w-full h-full overflow-visible" preserveAspectRatio="none" viewBox="0 0 100 20">
                            <polyline
                              fill="none"
                              stroke={target.uptime24h.uptime_pct >= 99.9 ? "#10b981" : "#f59e0b"}
                              strokeWidth="1.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              points={target.uptime24h.series
                                .map((p, i) => {
                                  const x = (i / (target.uptime24h!.series.length - 1)) * 100;
                                  const y = 20 - (p.up_pct / 100) * 20;
                                  return `${x},${y}`;
                                })
                                .join(" ")}
                            />
                          </svg>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="mt-6 border-t border-border-subtle pt-4">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-medium text-fg-secondary uppercase tracking-wider">
                        SLO Compliance
                      </p>
                      <button 
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setActiveTargetId(target.id); setEditingSLO(null); setShowSLOModal(true); }}
                        className="text-fg-muted hover:text-fg-primary p-0.5"
                        title="Add SLO"
                      >
                        <Plus size={12} />
                      </button>
                    </div>
                    {target.slos.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {target.slos.map((slo) => (
                          <div 
                            key={slo.id} 
                            className="group/slo relative flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-elevated px-2 py-1 text-xs"
                            title={`${slo.name} (${slo.objective_pct}%)`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${slo.status?.compliant ? 'bg-status-success' : 'bg-status-critical'}`} />
                            <span className="truncate max-w-[120px]">{slo.name}</span>
                            
                            <div className="hidden group-hover/slo:flex absolute right-0 top-0 bottom-0 bg-bg-elevated rounded-full items-center px-1 gap-1 border border-border-subtle shadow-sm">
                              <button 
                                onClick={(e) => { e.preventDefault(); e.stopPropagation(); setActiveTargetId(target.id); setEditingSLO(slo); setShowSLOModal(true); }}
                                className="text-fg-muted hover:text-fg-primary"
                              >
                                <Pencil size={10} />
                              </button>
                              <button 
                                onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteSLO(slo.id); }}
                                className="text-fg-muted hover:text-status-critical"
                              >
                                <Trash2 size={10} />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5 text-xs text-fg-muted">
                        <Shield size={12} /> No active SLOs
                      </div>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}

          {/* Maintenance Windows Section */}
          <div className="pt-8 border-t border-border-subtle">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-base font-semibold text-fg-primary">Maintenance Windows</h2>
                <p className="text-sm text-fg-secondary">Scheduled downtime periods where SLA alerts are suppressed.</p>
              </div>
              <Button size="sm" onClick={() => { setEditingMW(null); setShowMWModal(true); }}>
                <Plus size={14} /> Schedule Maintenance
              </Button>
            </div>

            {loading ? (
              <div className="h-40 rounded-xl border border-border-subtle bg-bg-elevated animate-pulse" />
            ) : maintenanceWindows.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-strong px-6 py-12 text-center">
                <Calendar className="mb-3 h-8 w-8 text-fg-muted" />
                <p className="text-sm font-medium text-fg-primary">No maintenance windows</p>
                <p className="mt-1 text-sm text-fg-secondary max-w-md">
                  Schedule maintenance to temporarily disable alerts and exclude time from SLA calculations.
                </p>
              </div>
            ) : (
              <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
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
                        <tr key={mw.id} className="hover:bg-bg-elevated transition-colors">
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-fg-primary">{mw.name}</span>
                              {isActive && (
                                <Badge className="bg-status-warning-bg text-status-warning border-status-warning-border">Active</Badge>
                              )}
                            </div>
                            {mw.reason && <p className="mt-0.5 text-xs text-fg-muted max-w-md truncate">{mw.reason}</p>}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-1">
                              {mw.target_ids.includes("*") ? (
                                <Badge variant="default" className="text-[10px]">All Targets</Badge>
                              ) : (
                                mw.target_ids.map(id => {
                                  const t = targets.find(t => t.id === id);
                                  return (
                                    <Badge key={id} variant="default" className="text-[10px]">
                                      {t?.name || id.slice(0, 8)}
                                    </Badge>
                                  );
                                })
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <p className="text-sm text-fg-primary">{start.toLocaleString()} - {end.toLocaleString()}</p>
                            {mw.rrule && <p className="mt-0.5 text-xs font-mono text-fg-muted">Repeats: {mw.rrule}</p>}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <button onClick={() => { setEditingMW(mw); setShowMWModal(true); }} className="text-fg-muted hover:text-fg-primary p-1" title="Edit">
                                <Pencil size={14} />
                              </button>
                              <button onClick={() => handleDeleteMW(mw.id)} className="text-fg-muted hover:text-status-critical p-1" title="Delete">
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

      <SLOModal
        open={showSLOModal}
        onClose={() => setShowSLOModal(false)}
        onSaved={loadData}
        targetId={activeTargetId}
        initialData={editingSLO}
      />
    </div>
  );
}
