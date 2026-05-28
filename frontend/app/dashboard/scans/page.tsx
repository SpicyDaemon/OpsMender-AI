"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  RefreshCw,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  createAuditRun,
  dismissAuditFinding,
  getAuditRun,
  listAuditRuns,
  listMCPServers,
  listSkills,
  remediateAuditFinding,
} from "@/lib/api";
import type {
  AuditFindingResponse,
  AuditFindingStatus,
  AuditRunResponse,
  AuditSeverity,
  MCPServerResponse,
  SkillResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const SEVERITY_VARIANT: Record<AuditSeverity, string> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  low: "low",
  info: "default",
};

const RUN_STATUS_VARIANT: Record<string, string> = {
  queued: "default",
  running: "active",
  completed: "completed",
  failed: "failed",
};

const FINDING_STATUS_VARIANT: Record<AuditFindingStatus, string> = {
  open: "info",
  remediating: "active",
  resolved: "completed",
  dismissed: "closed",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function EnvironmentScansPage() {
  const toast = useToast();
  const [runs, setRuns] = useState<AuditRunResponse[]>([]);
  const [selectedRun, setSelectedRun] = useState<AuditRunResponse | null>(null);
  const [findings, setFindings] = useState<AuditFindingResponse[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerResponse[]>([]);
  const [selectedMcpServer, setSelectedMcpServer] = useState<string>("");
  const [skills, setSkills] = useState<SkillResponse[]>([]);
  const [selectedFocusAreas, setSelectedFocusAreas] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [r, m, s] = await Promise.all([
        listAuditRuns(),
        listMCPServers(),
        listSkills(),
      ]);
      setRuns(r.items);
      setMcpServers(m.items);
      setSkills(s.items);
      if (!selectedMcpServer && m.items.length > 0) {
        setSelectedMcpServer(m.items[0].name);
      }
      if (!selectedRun && r.items.length > 0) {
        const detail = await getAuditRun(r.items[0].id);
        setSelectedRun(detail.run);
        setFindings(detail.findings);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [selectedRun, selectedMcpServer, toast]);

  // Focus areas for the selected MCP server come from its bound SKILL.md.
  const focusAreasForServer = (() => {
    const server = mcpServers.find((s) => s.name === selectedMcpServer);
    if (!server) return [] as string[];
    const skill = skills.find((sk) => sk.mcp_server_id === server.id);
    return skill?.focus_areas ?? [];
  })();

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openRun = useCallback(
    async (run: AuditRunResponse) => {
      try {
        const detail = await getAuditRun(run.id);
        setSelectedRun(detail.run);
        setFindings(detail.findings);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [toast],
  );

  const toggleFocusArea = (item: string) => {
    setSelectedFocusAreas((prev) =>
      prev.includes(item) ? prev.filter((k) => k !== item) : [...prev, item],
    );
  };

  const startRun = async () => {
    if (!selectedMcpServer) {
      toast.error("Pick an MCP server first");
      return;
    }
    setRunning(true);
    try {
      // The single default analyzer is "environment-scan". Pass the chosen
      // MCP server name as a param, and (optionally) the user's narrowed
      // focus areas — empty means "use whatever the SKILL.md says, or let
      // the LLM decide if no focus areas are configured".
      const analyzer_params: Record<string, Record<string, unknown>> = {
        "environment-scan": { mcp_server_name: selectedMcpServer },
      };
      if (selectedFocusAreas.length > 0) {
        analyzer_params["environment-scan"].focus_areas = selectedFocusAreas;
      }
      const run = await createAuditRun({
        analyzers: ["environment-scan"],
        analyzer_params,
        execute: true,
      });
      toast.success(`Scan ${run.status}: ${run.finding_count} finding(s)`);
      const detail = await getAuditRun(run.id);
      setSelectedRun(detail.run);
      setFindings(detail.findings);
      const r = await listAuditRuns();
      setRuns(r.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  const remediate = async (finding: AuditFindingResponse) => {
    try {
      const result = await remediateAuditFinding(finding.id);
      toast.success(`Session ${result.session_id.slice(0, 8)}… started`);
      if (selectedRun) {
        const detail = await getAuditRun(selectedRun.id);
        setFindings(detail.findings);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const dismiss = async (finding: AuditFindingResponse) => {
    const reason = window.prompt("Dismiss reason (optional):") ?? undefined;
    try {
      await dismissAuditFinding(finding.id, reason);
      toast.success("Finding dismissed");
      if (selectedRun) {
        const detail = await getAuditRun(selectedRun.id);
        setFindings(detail.findings);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const summary = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of findings) {
      counts[f.severity] = (counts[f.severity] ?? 0) + 1;
    }
    return counts;
  }, [findings]);

  if (loading) {
    return (
      <div className="p-6">
        <TableSkeleton rows={5} />
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Environment Scans"
        subtitle="Run read-only scans against an MCP server and triage findings without paging anyone. Analyzers only see tools the MCP server exposes — skill + tier enforcement still applies."
        actions={
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        }
      />

      <section className="rounded-lg border border-border-default bg-bg-surface p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-secondary">
          New scan
        </h2>
        <p className="mt-1 text-sm text-fg-secondary">
          Pick an MCP server. The scan is LLM-driven and platform-agnostic —
          it works against any deployment surface the MCP server can reach
          (Kubernetes, container services, monolithic VMs, …). Focus areas
          configured in the server&rsquo;s SKILL.md narrow what the LLM
          weighs.
        </p>

        <div className="mt-3 space-y-1">
          <label className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            MCP server
          </label>
          {mcpServers.length === 0 ? (
            <p className="text-sm text-status-medium">
              No MCP servers configured. Add one under{" "}
              <a className="underline" href="/dashboard/config#mcp">
                Config → MCP
              </a>{" "}
              first.
            </p>
          ) : (
            <select
              value={selectedMcpServer}
              onChange={(e) => setSelectedMcpServer(e.target.value)}
              className="w-full rounded-md border border-border-default bg-bg-elevated px-3 py-1.5 text-sm text-fg-primary"
            >
              {mcpServers.map((s) => (
                <option key={s.id} value={s.name}>
                  {s.name} · {s.transport}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="mt-4 space-y-1">
          <label className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            Focus areas{" "}
            <span className="font-normal normal-case text-fg-tertiary">
              (optional · from this server&rsquo;s SKILL.md)
            </span>
          </label>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {focusAreasForServer.length === 0 ? (
            <p className="text-xs text-fg-tertiary">
              No focus areas configured. The LLM will pick what to scan.
              Configure a <code>focus_areas:</code> list in the SKILL.md
              attached to this server to narrow future scans.
            </p>
          ) : (
            focusAreasForServer.map((area) => {
              const checked = selectedFocusAreas.includes(area);
              return (
                <button
                  key={area}
                  type="button"
                  onClick={() => toggleFocusArea(area)}
                  className={`rounded-md border px-3 py-1.5 text-sm transition ${
                    checked
                      ? "border-accent-default bg-accent-subtle text-accent-default"
                      : "border-border-default bg-bg-elevated text-fg-primary hover:border-accent-default/50"
                  }`}
                  title="Toggle to narrow this run"
                >
                  {area}
                </button>
              );
            })
          )}
        </div>
        <div className="mt-4 flex justify-end">
          <Button
            onClick={startRun}
            disabled={running || !selectedMcpServer}
          >
            <Sparkles className="h-4 w-4" />
            {running ? "Running…" : "Run scan"}
          </Button>
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <aside className="rounded-lg border border-border-default bg-bg-surface">
          <header className="border-b border-border-default px-3 py-2 text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Recent runs
          </header>
          {runs.length === 0 ? (
            <div className="p-3 text-sm text-fg-secondary">No audit runs yet.</div>
          ) : (
            <ul className="divide-y divide-border-default">
              {runs.map((run) => (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => openRun(run)}
                    className={`flex w-full flex-col gap-1 px-3 py-2 text-left transition hover:bg-bg-elevated ${
                      selectedRun?.id === run.id ? "bg-bg-elevated" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <Badge variant={(RUN_STATUS_VARIANT[run.status] ?? "default") as never}>
                        {run.status}
                      </Badge>
                      <span className="text-xs text-fg-secondary">
                        {run.finding_count} finding{run.finding_count === 1 ? "" : "s"}
                      </span>
                    </div>
                    <span className="truncate text-xs text-fg-secondary">
                      {run.analyzers.join(", ") || "—"}
                    </span>
                    <span className="text-[11px] text-fg-tertiary">
                      {fmtDate(run.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="space-y-3">
          {selectedRun ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-default bg-bg-surface px-4 py-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={(RUN_STATUS_VARIANT[selectedRun.status] ?? "default") as never}
                    >
                      {selectedRun.status}
                    </Badge>
                    <span className="text-sm font-semibold text-fg-primary">
                      {selectedRun.analyzers.join(", ") || "—"}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-fg-secondary">
                    Started {fmtDate(selectedRun.started_at)} · Finished{" "}
                    {fmtDate(selectedRun.finished_at)}
                  </div>
                  {selectedRun.error && (
                    <div className="mt-1 text-xs text-status-critical">
                      {selectedRun.error}
                    </div>
                  )}
                </div>
                <div className="flex gap-3 text-xs text-fg-secondary">
                  {Object.entries(summary).map(([sev, n]) => (
                    <span key={sev}>
                      <Badge variant={(SEVERITY_VARIANT[sev as AuditSeverity] ?? "default") as never}>
                        {sev}
                      </Badge>{" "}
                      {n}
                    </span>
                  ))}
                </div>
              </div>

              {findings.length === 0 ? (
                <EmptyState
                  title="No findings"
                  description="The run completed without surfacing any anomalies."
                  learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
                  learnMoreLabel="Operator guide"
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-border-default">
                  <table className="w-full text-sm">
                    <thead className="bg-bg-elevated text-left text-xs uppercase tracking-wide text-fg-secondary">
                      <tr>
                        <th className="px-3 py-2">Severity</th>
                        <th className="px-3 py-2">Analyzer</th>
                        <th className="px-3 py-2">Resource</th>
                        <th className="px-3 py-2">Message</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-default bg-bg-surface">
                      {findings.map((f) => (
                        <tr key={f.id}>
                          <td className="px-3 py-2">
                            <Badge variant={(SEVERITY_VARIANT[f.severity] ?? "default") as never}>
                              {f.severity}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-fg-secondary">{f.analyzer}</td>
                          <td className="px-3 py-2 text-fg-secondary">
                            {f.resource ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-fg-primary">
                            <div className="line-clamp-3">{f.message}</div>
                            {f.suggested_fix && (
                              <div className="mt-1 text-xs text-fg-tertiary">
                                Hint: {f.suggested_fix}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <Badge variant={(FINDING_STATUS_VARIANT[f.status] ?? "default") as never}>
                              {f.status}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-right">
                            {f.status === "open" && (
                              <div className="flex justify-end gap-1">
                                <Button
                                  variant="ghost"
                                  onClick={() => remediate(f)}
                                  title="Spawn a remediation session"
                                >
                                  <CheckCircle2 className="h-4 w-4" /> Fix with AI
                                </Button>
                                <Button
                                  variant="ghost"
                                  onClick={() => dismiss(f)}
                                  title="Dismiss finding"
                                >
                                  <XCircle className="h-4 w-4" />
                                </Button>
                              </div>
                            )}
                            {f.status === "remediating" && f.session_id && (
                              <a
                                href={`/dashboard/sessions/detail?id=${f.session_id}`}
                                className="text-xs text-accent-default hover:underline"
                              >
                                View session →
                              </a>
                            )}
                            {f.status === "dismissed" && f.dismiss_reason && (
                              <span
                                className="text-xs text-fg-tertiary"
                                title={f.dismiss_reason}
                              >
                                <Trash2 className="h-3 w-3 inline" />{" "}
                                {f.dismiss_reason.slice(0, 40)}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              title="No audit selected"
              description="Run a new audit above or pick an existing one from the list."
              learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
              learnMoreLabel="Operator guide"
            />
          )}
        </div>
      </section>
    </div>
  );
}
