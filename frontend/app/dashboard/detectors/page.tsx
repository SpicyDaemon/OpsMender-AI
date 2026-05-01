"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Eye,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Zap,
} from "lucide-react";
import {
  createDetector,
  deleteDetector,
  listDetectorHistory,
  listDetectors,
  listDetectorTemplates,
  listMCPServers,
  listModelConfigs,
  runDetector,
  updateDetector,
} from "@/lib/api";
import type {
  DetectorHistoryResponse,
  DetectorRuleResponse,
  DetectorTemplateResponse,
  MCPServerResponse,
  ModelConfigResponse,
  Severity,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { CardSkeleton, SkeletonText, TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

type FormState = {
  name: string;
  mcp_server_id: string;
  model_config_id: string;
  interval_seconds: string;
  severity_default: Severity;
  is_active: boolean;
  prompt_template: string;
};

function fmtDate(iso: string | null) {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtInterval(seconds: number) {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function timeSince(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "just now";
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function toForm(rule: DetectorRuleResponse | null): FormState {
  return {
    name: rule?.name ?? "",
    mcp_server_id: rule?.mcp_server_id ?? "",
    model_config_id: rule?.model_config_id ?? "",
    interval_seconds: String(rule?.interval_seconds ?? 300),
    severity_default: rule?.severity_default ?? "medium",
    is_active: rule?.is_active ?? true,
    prompt_template: rule?.prompt_template ?? "",
  };
}

// ---------------------------------------------------------------------------
// Mini sparkline SVG — shows last N runs as dots/bars
// ---------------------------------------------------------------------------

function RunSparkline({ history }: { history: DetectorHistoryResponse[] }) {
  // Show last 10 runs as small dots
  const recent = history.slice(0, 10).reverse();
  if (recent.length === 0) {
    return <span className="text-[10px] text-fg-muted">no runs</span>;
  }

  const w = 80;
  const h = 16;
  const dotR = 3;
  const gap = w / (recent.length + 1);

  return (
    <div className="inline-flex items-center gap-1.5" title={`Last ${recent.length} runs`}>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="block">
        {recent.map((run, i) => {
          const cx = gap * (i + 1);
          const cy = h / 2;
          let fill: string;
          if (run.error) {
            fill = "var(--color-status-critical)";
          } else if (run.issue_detected) {
            fill = "var(--color-status-high)";
          } else {
            fill = "var(--color-status-low)";
          }
          return (
            <circle key={run.id} cx={cx} cy={cy} r={dotR} fill={fill} opacity={0.9}>
              <title>
                {new Date(run.ran_at).toLocaleString()}
                {run.error ? ` (error: ${run.error})` : run.issue_detected ? " (issue)" : " (ok)"}
              </title>
            </circle>
          );
        })}
      </svg>
      <span className="text-[10px] text-fg-muted tabular-nums">{recent.length}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status indicator per rule
// ---------------------------------------------------------------------------

function StatusIndicator({
  rule,
  runningId,
}: {
  rule: DetectorRuleResponse;
  runningId: string | null;
}) {
  const isRunning = runningId === rule.id;

  if (isRunning) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-accent font-medium">
        <span className="h-2 w-2 rounded-full bg-accent animate-pulse" />
        Running
      </span>
    );
  }

  if (!rule.is_active) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
        <Pause size={12} className="text-fg-muted" />
        Paused
      </span>
    );
  }

  if (rule.last_ran_at) {
    // eslint-disable-next-line react-hooks/purity
    const msSince = Date.now() - new Date(rule.last_ran_at).getTime();
    const overdue = msSince > rule.interval_seconds * 1000 * 2;

    if (overdue) {
      return (
        <span className="inline-flex items-center gap-1.5 text-xs text-status-high font-medium">
          <AlertTriangle size={12} />
          Overdue
        </span>
      );
    }

    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-status-low">
        <CheckCircle2 size={12} />
        Idle
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
      <span className="h-2 w-2 rounded-full bg-fg-muted" />
      Waiting
    </span>
  );
}

// ---------------------------------------------------------------------------
// DetectorModal (unchanged logic, visual tweaks)
// ---------------------------------------------------------------------------

function DetectorModal({
  open,
  rule,
  templates,
  servers,
  models,
  onClose,
  onSaved,
}: {
  open: boolean;
  rule: DetectorRuleResponse | null;
  templates: DetectorTemplateResponse[];
  servers: MCPServerResponse[];
  models: ModelConfigResponse[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<FormState>(toForm(rule));
  const [templateKey, setTemplateKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setForm(toForm(rule));
    setTemplateKey("");
    setError("");
  }, [open, rule]);

  if (!open) return null;

  function applyTemplate(key: string) {
    setTemplateKey(key);
    const tpl = templates.find((item) => item.key === key);
    if (!tpl) return;
    setForm((prev) => ({
      ...prev,
      interval_seconds: String(tpl.interval_seconds),
      severity_default: tpl.severity_default,
      prompt_template: tpl.prompt_template,
      name: prev.name || tpl.key.replace(/_/g, "-"),
    }));
  }

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    if (!form.mcp_server_id) {
      setError("MCP server is required");
      return;
    }
    if (!form.prompt_template.trim()) {
      setError("Prompt template is required");
      return;
    }
    const interval = Number(form.interval_seconds);
    if (!Number.isFinite(interval) || interval < 30 || interval > 86400) {
      setError("Interval must be between 30 and 86400 seconds");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const payload = {
        name: form.name.trim(),
        mcp_server_id: form.mcp_server_id,
        model_config_id: form.model_config_id || null,
        interval_seconds: interval,
        severity_default: form.severity_default,
        is_active: form.is_active,
        prompt_template: form.prompt_template.trim(),
      };
      if (rule) {
        await updateDetector(rule.id, payload);
      } else {
        await createDetector(payload);
      }
      await onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={rule ? "Edit detector rule" : "Add detector rule"}
      maxWidth="max-w-3xl"
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="detector-name">Rule name</Label>
            <Input
              id="detector-name"
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              placeholder="prod-crashloop-watch"
            />
          </div>
          <div>
            <Label htmlFor="detector-template">Built-in template</Label>
            <Select
              id="detector-template"
              value={templateKey}
              onChange={(e) => applyTemplate(e.target.value)}
            >
              <option value="">Custom / none</option>
              {templates.map((tpl) => (
                <option key={tpl.key} value={tpl.key}>
                  {tpl.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="detector-mcp">MCP server</Label>
            <Select
              id="detector-mcp"
              value={form.mcp_server_id}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, mcp_server_id: e.target.value }))
              }
            >
              <option value="">Select an MCP server</option>
              {servers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="detector-model">Model config</Label>
            <Select
              id="detector-model"
              value={form.model_config_id}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, model_config_id: e.target.value }))
              }
            >
              <option value="">Default model config</option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                  {model.is_default ? " (default)" : ""}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div>
            <Label htmlFor="detector-interval">Interval (seconds)</Label>
            <Input
              id="detector-interval"
              type="number"
              min={30}
              max={86400}
              value={form.interval_seconds}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, interval_seconds: e.target.value }))
              }
            />
          </div>
          <div>
            <Label htmlFor="detector-severity">Default severity</Label>
            <Select
              id="detector-severity"
              value={form.severity_default}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  severity_default: e.target.value as Severity,
                }))
              }
            >
              <option value="critical">critical</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="detector-active">Status</Label>
            <Select
              id="detector-active"
              value={form.is_active ? "active" : "inactive"}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  is_active: e.target.value === "active",
                }))
              }
            >
              <option value="active">active</option>
              <option value="inactive">inactive</option>
            </Select>
          </div>
        </div>

        <div>
          <Label htmlFor="detector-prompt">Prompt template</Label>
          <Textarea
            id="detector-prompt"
            rows={12}
            value={form.prompt_template}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, prompt_template: e.target.value }))
            }
            className="text-sm"
          />
          <p className="mt-1 text-xs text-fg-secondary">
            The detector will use this prompt to decide what to observe on the MCP
            server and whether to file an incident.
          </p>
        </div>

        <FormError message={error} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            {rule ? "Save changes" : "Create detector"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function HistoryModal({
  open,
  rule,
  history,
  loading,
  onClose,
}: {
  open: boolean;
  rule: DetectorRuleResponse | null;
  history: DetectorHistoryResponse[];
  loading: boolean;
  onClose: () => void;
}) {
  if (!open || !rule) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Run history: ${rule.name}`}
      maxWidth="max-w-3xl"
    >
      {loading ? (
        <div className="space-y-3">
          <SkeletonText lines={2} />
          <SkeletonText lines={2} />
          <SkeletonText lines={2} />
        </div>
      ) : (
        <div className="space-y-3">
          {history.length === 0 ? (
            <p className="text-sm text-fg-secondary">No run history yet.</p>
          ) : (
            history.map((item) => (
              <div
                key={item.id}
                className={`rounded-xl border p-4 ${
                  item.error
                    ? "border-status-critical-border bg-status-critical-bg/20"
                    : item.issue_detected
                      ? "border-status-high-border bg-status-high-bg/20"
                      : "border-border-subtle bg-bg-elevated"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    {item.error ? (
                      <Badge variant="rejected">error</Badge>
                    ) : item.issue_detected ? (
                      <Badge variant="high">issue detected</Badge>
                    ) : (
                      <Badge variant="approved">no issue</Badge>
                    )}
                    {item.duration_ms != null && (
                      <span className="text-[10px] text-fg-muted font-mono tabular-nums bg-bg-elevated rounded px-1.5 py-0.5">
                        {item.duration_ms}ms
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-fg-secondary tabular-nums font-mono">{fmtDate(item.ran_at)}</p>
                </div>
                {item.incident_id && (
                  <p className="mt-2 text-xs text-fg-secondary">
                    <span className="text-fg-muted">Incident:</span>{" "}
                    <span className="font-mono">{item.incident_id.slice(0, 8)}…</span>
                  </p>
                )}
                {item.error && (
                  <p className="mt-2 text-xs text-status-critical">
                    {item.error}
                  </p>
                )}
                {item.raw_verdict && (
                  <pre className="mt-3 overflow-x-auto rounded-lg border border-border-subtle bg-bg-base p-3 text-xs text-fg-primary font-mono leading-relaxed">
                    {JSON.stringify(item.raw_verdict, null, 2)}
                  </pre>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </Modal>
  );
}

export default function DetectorsPage() {
  const { user } = useAuth();
  const canManage = user?.role === "admin";
  const canRun = user?.role === "admin" || user?.role === "operator";

  const [loading, setLoading] = useState(true);
  const [rules, setRules] = useState<DetectorRuleResponse[]>([]);
  const [templates, setTemplates] = useState<DetectorTemplateResponse[]>([]);
  const [servers, setServers] = useState<MCPServerResponse[]>([]);
  const [models, setModels] = useState<ModelConfigResponse[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<DetectorRuleResponse | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRule, setHistoryRule] = useState<DetectorRuleResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [history, setHistory] = useState<DetectorHistoryResponse[]>([]);
  const [runningId, setRunningId] = useState<string | null>(null);
  // Per-rule sparkline history (loaded in bulk)
  const [sparklineData, setSparklineData] = useState<Record<string, DetectorHistoryResponse[]>>({});
  const toast = useToast();

  const templateMap = useMemo(
    () => new Map(templates.map((tpl) => [tpl.prompt_template, tpl])),
    [templates],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ruleRes, templateRes, serverRes, modelRes] = await Promise.all([
        listDetectors(),
        listDetectorTemplates(),
        listMCPServers(),
        listModelConfigs(),
      ]);
      setRules(ruleRes.items);
      setTemplates(templateRes.items);
      setServers(serverRes.items.filter((item) => item.is_active));
      setModels(modelRes.items);

      // Fetch sparkline history for each rule (last 10 runs)
      const sparklines: Record<string, DetectorHistoryResponse[]> = {};
      await Promise.allSettled(
        ruleRes.items.map(async (rule) => {
          try {
            const hist = await listDetectorHistory(rule.id);
            sparklines[rule.id] = hist.items.slice(0, 10);
          } catch {
            sparklines[rule.id] = [];
          }
        }),
      );
      setSparklineData(sparklines);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load detectors");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    setModalOpen(true);
  }

  function openEdit(rule: DetectorRuleResponse) {
    setEditing(rule);
    setModalOpen(true);
  }

  async function handleDelete(rule: DetectorRuleResponse) {
    if (!window.confirm(`Delete detector rule "${rule.name}"?`)) return;
    try {
      await deleteDetector(rule.id);
      toast.success(`Deleted "${rule.name}"`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRun(rule: DetectorRuleResponse) {
    setRunningId(rule.id);
    try {
      const res = await runDetector(rule.id);
      await load();
      if (!res.success) {
        toast.error(`Run failed: ${res.error ?? "unknown error"}`);
      } else if (res.issue_detected) {
        toast.warning(
          `Run complete — incident detected${res.incident_id ? `: ${res.incident_id.slice(0, 8)}…` : ""}`,
        );
      } else {
        toast.success("Run complete — no incident");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Run failed");
    } finally {
      setRunningId(null);
    }
  }

  async function openHistory(rule: DetectorRuleResponse) {
    setHistoryRule(rule);
    setHistory([]);
    setHistoryLoading(true);
    setHistoryOpen(true);
    try {
      const res = await listDetectorHistory(rule.id);
      setHistory(res.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load history");
      setHistoryOpen(false);
      setHistoryRule(null);
    } finally {
      setHistoryLoading(false);
    }
  }

  // Summary counts
  const activeCount = rules.filter((r) => r.is_active).length;
  const inactiveCount = rules.length - activeCount;

  if (loading) {
    return (
      <div className="mx-auto max-w-7xl space-y-8">
        <CardSkeleton lines={2} />
        <TableSkeleton rows={5} columns={7} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-accent-bg border border-status-info-border">
            <Activity size={18} className="text-accent" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-fg-primary">Detectors</h1>
            <p className="mt-0.5 text-sm text-fg-secondary">
              {rules.length} rules · {activeCount} active · {inactiveCount} paused
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load}>
            <RefreshCw size={14} />
            Refresh
          </Button>
          {canManage && (
            <Button size="sm" onClick={openCreate}>
              <Plus size={14} />
              Add detector
            </Button>
          )}
        </div>
      </div>

      {/* Built-in templates */}
      <section className="rounded-2xl border border-border-subtle bg-bg-panel p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Zap size={16} className="text-accent" />
          <h2 className="text-sm font-semibold text-fg-primary">Built-in templates</h2>
          <span className="text-xs text-fg-muted ml-1">{templates.length} available</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {templates.map((tpl) => (
            <div
              key={tpl.key}
              className="rounded-xl border border-border-subtle bg-bg-elevated p-4 hover:border-border-strong transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-fg-primary">{tpl.label}</h3>
                  <p className="mt-1 text-sm text-fg-secondary">{tpl.description}</p>
                </div>
                <Badge variant={tpl.severity_default}>{tpl.severity_default}</Badge>
              </div>
              <p className="mt-3 text-xs text-fg-muted tabular-nums font-mono">
                interval: {fmtInterval(tpl.interval_seconds)}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Detector rules table */}
      <section className="rounded-2xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-fg-primary">Detector rules</h2>
            <p className="text-xs text-fg-secondary">{rules.length} configured</p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium uppercase tracking-wide text-fg-secondary">
                <th className="px-5 py-3">Rule</th>
                <th className="px-5 py-3">MCP Server</th>
                <th className="px-5 py-3">Interval</th>
                <th className="px-5 py-3">Severity</th>
                <th className="px-5 py-3">Last Run</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Recent Runs</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {rules.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-6">
                    <EmptyState
                      icon={Activity}
                      title="No detector rules yet"
                      description="Detector rules poll an MCP server on a schedule and auto-file incidents when the agent spots something unusual."
                      action={
                        canManage ? (
                          <Button size="sm" onClick={openCreate}>
                            <Plus size={14} />
                            Add detector
                          </Button>
                        ) : undefined
                      }
                    />
                  </td>
                </tr>
              )}
              {rules.map((rule) => {
                const server = servers.find((item) => item.id === rule.mcp_server_id);
                const model = models.find((item) => item.id === rule.model_config_id);
                const template = templateMap.get(rule.prompt_template);
                const ruleSparkline = sparklineData[rule.id] ?? [];
                return (
                  <tr
                    key={rule.id}
                    className={`align-top transition-colors ${
                      !rule.is_active ? "opacity-60" : "hover:bg-bg-elevated"
                    }`}
                  >
                    <td className="px-5 py-4">
                      <div className="space-y-1">
                        <p className="font-medium text-fg-primary">{rule.name}</p>
                        <p className="max-w-md text-xs text-fg-secondary">
                          {template?.label ?? "Custom template"}
                          {model ? ` · model: ${model.name}` : " · default model"}
                        </p>
                        <p className="max-w-xl line-clamp-2 text-xs text-fg-muted">
                          {rule.prompt_template}
                        </p>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-sm text-fg-secondary">
                      {server?.name ?? rule.mcp_server_id.slice(0, 8)}
                    </td>
                    <td className="px-5 py-4 text-sm text-fg-secondary tabular-nums font-mono">
                      {fmtInterval(rule.interval_seconds)}
                    </td>
                    <td className="px-5 py-4">
                      <Badge variant={rule.severity_default}>{rule.severity_default}</Badge>
                    </td>
                    <td className="px-5 py-4">
                      <div className="text-sm text-fg-secondary">{fmtDate(rule.last_ran_at)}</div>
                      <div className="mt-0.5 text-[10px] text-fg-muted tabular-nums font-mono">
                        {timeSince(rule.last_ran_at)}
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusIndicator rule={rule} runningId={runningId} />
                    </td>
                    <td className="px-5 py-4">
                      <RunSparkline history={ruleSparkline} />
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex justify-end gap-2">
                        {canRun && (
                          <Button
                            size="sm"
                            variant="ghost"
                            loading={runningId === rule.id}
                            onClick={() => handleRun(rule)}
                          >
                            <Play size={14} />
                            Run now
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openHistory(rule)}
                        >
                          <Eye size={14} />
                          History
                        </Button>
                        {canManage && (
                          <>
                            <Button size="sm" variant="ghost" onClick={() => openEdit(rule)}>
                              Edit
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => handleDelete(rule)}>
                              <Trash2 size={14} />
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <DetectorModal
        open={modalOpen}
        rule={editing}
        templates={templates}
        servers={servers}
        models={models}
        onClose={() => setModalOpen(false)}
        onSaved={load}
      />

      <HistoryModal
        open={historyOpen}
        rule={historyRule}
        history={history}
        loading={historyLoading}
        onClose={() => {
          setHistoryOpen(false);
          setHistoryRule(null);
        }}
      />
    </div>
  );
}
