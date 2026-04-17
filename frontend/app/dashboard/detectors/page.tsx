"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Eye,
  Play,
  Plus,
  RefreshCw,
  Trash2,
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
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageSpinner } from "@/components/ui/Spinner";

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
          <p className="mt-1 text-xs text-gray-500">
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
        <PageSpinner />
      ) : (
        <div className="space-y-3">
          {history.length === 0 ? (
            <p className="text-sm text-gray-500">No run history yet.</p>
          ) : (
            history.map((item) => (
              <div
                key={item.id}
                className="rounded-xl border border-gray-200 bg-gray-50 p-4"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Badge variant={item.issue_detected ? "high" : "default"}>
                      {item.issue_detected ? "issue detected" : "no issue"}
                    </Badge>
                    {item.error && <Badge variant="rejected">error</Badge>}
                  </div>
                  <p className="text-xs text-gray-500">{fmtDate(item.ran_at)}</p>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-3 text-xs text-gray-600">
                  <div>Duration: {item.duration_ms ?? 0}ms</div>
                  <div>Incident: {item.incident_id ? `${item.incident_id.slice(0, 8)}…` : "none"}</div>
                  <div>Error: {item.error ?? "none"}</div>
                </div>
                {item.raw_verdict && (
                  <pre className="mt-3 overflow-x-auto rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-700">
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
  const [error, setError] = useState("");
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

  const templateMap = useMemo(
    () => new Map(templates.map((tpl) => [tpl.prompt_template, tpl])),
    [templates],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load detectors");
    } finally {
      setLoading(false);
    }
  }, []);

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
      await load();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRun(rule: DetectorRuleResponse) {
    setRunningId(rule.id);
    try {
      const res = await runDetector(rule.id);
      await load();
      window.alert(
        res.success
          ? res.issue_detected
            ? `Run complete. Incident detected${res.incident_id ? `: ${res.incident_id}` : ""}.`
            : "Run complete. No incident detected."
          : `Run failed: ${res.error ?? "unknown error"}`,
      );
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Run failed");
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
      window.alert(err instanceof Error ? err.message : "Failed to load history");
      setHistoryOpen(false);
      setHistoryRule(null);
    } finally {
      setHistoryLoading(false);
    }
  }

  if (loading) return <PageSpinner />;

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Detectors</h1>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            Turn MCP servers into incident sources with scheduled observation-only
            rules. Start from a built-in template or write a custom prompt.
          </p>
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

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Activity size={16} className="text-indigo-600" />
          <h2 className="text-sm font-semibold text-gray-900">Built-in templates</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {templates.map((tpl) => (
            <div
              key={tpl.key}
              className="rounded-xl border border-gray-200 bg-gray-50 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">{tpl.label}</h3>
                  <p className="mt-1 text-sm text-gray-500">{tpl.description}</p>
                </div>
                <Badge variant={tpl.severity_default}>{tpl.severity_default}</Badge>
              </div>
              <p className="mt-3 text-xs text-gray-500">
                Suggested interval: {fmtInterval(tpl.interval_seconds)}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">Detector rules</h2>
            <p className="text-xs text-gray-500">{rules.length} configured</p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-5 py-3">Rule</th>
                <th className="px-5 py-3">MCP Server</th>
                <th className="px-5 py-3">Interval</th>
                <th className="px-5 py-3">Severity</th>
                <th className="px-5 py-3">Last Run</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rules.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-5 py-10 text-center text-sm text-gray-400">
                    No detector rules yet.
                  </td>
                </tr>
              )}
              {rules.map((rule) => {
                const server = servers.find((item) => item.id === rule.mcp_server_id);
                const model = models.find((item) => item.id === rule.model_config_id);
                const template = templateMap.get(rule.prompt_template);
                return (
                  <tr key={rule.id} className="align-top">
                    <td className="px-5 py-4">
                      <div className="space-y-1">
                        <p className="font-medium text-gray-900">{rule.name}</p>
                        <p className="max-w-md text-xs text-gray-500">
                          {template?.label ?? "Custom template"}
                          {model ? ` • model: ${model.name}` : " • default model"}
                        </p>
                        <p className="max-w-xl line-clamp-2 text-xs text-gray-400">
                          {rule.prompt_template}
                        </p>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-600">
                      {server?.name ?? rule.mcp_server_id.slice(0, 8)}
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-600">
                      {fmtInterval(rule.interval_seconds)}
                    </td>
                    <td className="px-5 py-4">
                      <Badge variant={rule.severity_default}>{rule.severity_default}</Badge>
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-600">
                      <div>{fmtDate(rule.last_ran_at)}</div>
                      <div className="mt-1 text-xs text-gray-400">
                        {rule.last_fingerprint ? `fp: ${rule.last_fingerprint}` : "no fingerprint"}
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <Badge variant={rule.is_active ? "approved" : "paused"}>
                        {rule.is_active ? "active" : "inactive"}
                      </Badge>
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
