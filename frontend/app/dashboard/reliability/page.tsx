"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Plus, ServerCrash, Shield, Clock } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  listSLATargets,
  getSLATargetUptime,
  listSLOs,
  getSLOStatus,
  listMaintenanceWindows,
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
  slos: (SLOResponse & { status: SLOStatusResponse | null })[];
  activeMaintenance: MaintenanceWindowResponse | null;
}

export default function ReliabilityPage() {
  const [targets, setTargets] = useState<TargetWithMetrics[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
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
              slos: slosWithStatus,
              activeMaintenance: activeMw,
            };
          })
        );

        if (!cancelled) {
          setTargets(targetMetrics);
        }
      } catch (err) {
        console.error("Failed to load reliability data:", err);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
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
          <Button disabled>
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
                  href={`/dashboard/reliability/${target.id}`}
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
                        <div className={`h-2 w-2 rounded-full ${target.is_active ? "bg-status-success" : "bg-fg-muted"}`} />
                      )}
                    </div>
                    
                    <div className="mt-4 flex items-end gap-2">
                      <span className="text-3xl font-light tracking-tight text-fg-primary">
                        {target.uptime30d ? target.uptime30d.uptime_pct.toFixed(2) : "100.00"}%
                      </span>
                      <span className="text-xs text-fg-secondary mb-1">30d uptime</span>
                    </div>
                  </div>

                  <div className="mt-6 border-t border-border-subtle pt-4">
                    <p className="text-xs font-medium text-fg-secondary mb-2 uppercase tracking-wider">
                      SLO Compliance
                    </p>
                    {target.slos.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {target.slos.map((slo) => (
                          <div 
                            key={slo.id} 
                            className="flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-elevated px-2 py-1 text-xs"
                            title={`${slo.name} (${slo.objective_pct}%)`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${slo.status?.compliant ? 'bg-status-success' : 'bg-status-critical'}`} />
                            <span className="truncate max-w-[120px]">{slo.name}</span>
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
        </div>
      </main>
    </div>
  );
}
