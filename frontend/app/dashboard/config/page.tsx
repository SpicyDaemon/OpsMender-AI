"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ClipboardCopy,
  Key,
  Pencil,
  Plug,
  Plus,
  Save,
  ShieldOff,
  Star,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  createIngestToken,
  createMCPServer,
  createModelConfig,
  deleteIngestToken,
  deleteMCPServer,
  deleteModelConfig,
  getConfig,
  listIngestProviders,
  listIngestTokens,
  listMCPServers,
  listModelConfigs,
  listProviders,
  revokeIngestToken,
  setDefaultModelConfig,
  testMCPServer,
  updateConfig,
  updateMCPServer,
  updateModelConfigById,
} from "@/lib/api";
import type {
  ConfigResponse,
  IngestProviderItem,
  IngestTokenCreate,
  IngestTokenCreatedResponse,
  IngestTokenResponse,
  MCPServerResponse,
  MCPServerTestResponse,
  MCPServerUpsert,
  MCPTransport,
  ModelConfigResponse,
  ModelConfigUpdate,
  ProviderModelsResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageSpinner } from "@/components/ui/Spinner";


function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-6 py-4">
        <h2 className="text-base font-semibold text-gray-900">{title}</h2>
        {description && (
          <p className="mt-0.5 text-sm text-gray-500">{description}</p>
        )}
      </div>
      <div className="space-y-4 px-6 py-5">{children}</div>
    </div>
  );
}

function TierSection({
  config,
  onSaved,
  canEdit,
}: {
  config: ConfigResponse;
  onSaved: () => Promise<void>;
  canEdit: boolean;
}) {
  const [tier, setTier] = useState(String(config.tier));
  const [logLevel, setLogLevel] = useState(config.logging_level);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    setTier(String(config.tier));
    setLogLevel(config.logging_level);
  }, [config]);

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await updateConfig({ tier: Number(tier), logging_level: logLevel });
      setSuccess(true);
      await onSaved();
      setTimeout(() => setSuccess(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section
      title="Runtime Config"
      description="Global tier controls the maximum action tier allowed without explicit override."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <Label htmlFor="cfg-tier">Global Tier</Label>
          <Select
            id="cfg-tier"
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            disabled={!canEdit}
          >
            <option value="0">Tier 0 — Full sandbox (all ops permitted)</option>
            <option value="1">Tier 1 — Approval gate (destructive ops need approval)</option>
            <option value="2">Tier 2 — Safe + caution only (no destructive ops)</option>
            <option value="3">Tier 3 — Advise-only (no execution)</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="cfg-log">Log Level</Label>
          <Select
            id="cfg-log"
            value={logLevel}
            onChange={(e) => setLogLevel(e.target.value)}
            disabled={!canEdit}
          >
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </Select>
        </div>
      </div>

      <div>
        <Label>Audit Output</Label>
        <p className="rounded-md bg-gray-50 px-3 py-2 font-mono text-sm text-gray-500">
          {config.audit_output}
        </p>
      </div>

      {!canEdit && (
        <p className="text-sm text-gray-500">
          Admin role required to edit runtime config.
        </p>
      )}
      {error && <FormError message={error} />}
      {success && <p className="text-sm text-green-600">Saved successfully.</p>}

      <div className="flex justify-end">
        <Button onClick={handleSave} loading={saving} disabled={!canEdit}>
          <Save size={13} /> Save
        </Button>
      </div>
    </Section>
  );
}

function IngestAutoStartSection({
  config,
  onSaved,
  canEdit,
}: {
  config: ConfigResponse;
  onSaved: () => Promise<void>;
  canEdit: boolean;
}) {
  const [enabled, setEnabled] = useState(config.ingest_auto_start_enabled);
  const [minSeverity, setMinSeverity] = useState(config.ingest_auto_start_min_severity);
  const [source, setSource] = useState(config.ingest_auto_start_source ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    setEnabled(config.ingest_auto_start_enabled);
    setMinSeverity(config.ingest_auto_start_min_severity);
    setSource(config.ingest_auto_start_source ?? "");
  }, [
    config.ingest_auto_start_enabled,
    config.ingest_auto_start_min_severity,
    config.ingest_auto_start_source,
  ]);

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await updateConfig({
        ingest_auto_start_enabled: enabled,
        ingest_auto_start_min_severity: minSeverity,
        ingest_auto_start_source: source.trim(),
      });
      setSuccess(true);
      await onSaved();
      setTimeout(() => setSuccess(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section
      title="Ingest Auto-Start"
      description="Optionally create a session automatically when a newly ingested incident matches the rule below."
    >
      <label className="flex items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          disabled={!canEdit}
        />
        <div>
          <p className="text-sm font-medium text-gray-900">
            Enable automatic session creation for ingested incidents
          </p>
          <p className="mt-1 text-sm text-gray-500">
            When enabled, AIM creates one session for a newly created incident if its severity
            meets the threshold and its source matches the configured provider key.
          </p>
        </div>
      </label>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <Label htmlFor="ingest-auto-severity">Minimum Severity</Label>
          <Select
            id="ingest-auto-severity"
            value={minSeverity}
            onChange={(e) => setMinSeverity(e.target.value as ConfigResponse["ingest_auto_start_min_severity"])}
            disabled={!canEdit}
          >
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="ingest-auto-source">Source Filter</Label>
          <Input
            id="ingest-auto-source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="legacy_alert_vendor"
            disabled={!canEdit}
          />
          <p className="mt-1 text-xs text-gray-400">
            Exact provider key such as `cloudwatch`, `azure_monitor`, `legacy_alert_vendor`, `legacy_alert_relay`, or `generic`.
            Leave blank to match any source.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
        Auto-start only runs for newly created incidents and reuses the current runtime tier for the session.
        Duplicate ingests will not spawn extra active sessions.
      </div>

      {!canEdit && (
        <p className="text-sm text-gray-500">
          Admin role required to edit ingest auto-start settings.
        </p>
      )}
      {error && <FormError message={error} />}
      {success && <p className="text-sm text-green-600">Saved successfully.</p>}

      <div className="flex justify-end">
        <Button onClick={handleSave} loading={saving} disabled={!canEdit}>
          <Save size={13} /> Save
        </Button>
      </div>
    </Section>
  );
}

type ModelFormState = {
  name: string;
  provider: string;
  model_id: string;
  api_key_env_var: string;
  base_url: string;
  api_version: string;
  max_tokens: number;
  temperature: number;
};

function createModelFormState(
  providers: ProviderModelsResponse[],
  current?: ModelConfigResponse | null,
): ModelFormState {
  const fallbackProvider = providers[0];
  const selectedProvider =
    providers.find((item) => item.provider === current?.provider) ?? fallbackProvider;

  return {
    name: current?.name ?? "",
    provider: current?.provider ?? selectedProvider?.provider ?? "anthropic",
    model_id:
      current?.model_id ??
      selectedProvider?.default_model_id ??
      "",
    api_key_env_var:
      current?.api_key_env_var ??
      selectedProvider?.default_api_key_env_var ??
      "",
    base_url: current?.base_url ?? "",
    api_version: current?.api_version ?? "",
    max_tokens: current?.max_tokens ?? 4096,
    temperature: current?.temperature ?? 0,
  };
}

function ModelConfigModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  providers,
  initialConfig,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: ModelFormState) => Promise<void>;
  saving: boolean;
  error: string;
  providers: ProviderModelsResponse[];
  initialConfig: ModelConfigResponse | null;
}) {
  const [form, setForm] = useState<ModelFormState>(() =>
    createModelFormState(providers, initialConfig),
  );

  useEffect(() => {
    if (!open) return;
    setForm(createModelFormState(providers, initialConfig));
  }, [open, providers, initialConfig]);

  const selectedProvider = providers.find(
    (provider) => provider.provider === form.provider,
  );

  function setField<K extends keyof ModelFormState>(
    key: K,
    value: ModelFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handleProviderChange(providerName: string) {
    const nextProvider = providers.find((item) => item.provider === providerName);
    setForm((current) => ({
      ...current,
      provider: providerName,
      model_id: nextProvider?.default_model_id ?? current.model_id,
      api_key_env_var:
        nextProvider?.default_api_key_env_var ?? current.api_key_env_var,
      base_url: "",
      api_version: "",
    }));
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    await onSubmit(form);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialConfig ? "Edit Model Config" : "Add Model Config"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="model-name">Display Name</Label>
            <Input
              id="model-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="primary-openai"
              required
            />
          </div>
          <div>
            <Label htmlFor="model-provider">Provider</Label>
            <Select
              id="model-provider"
              value={form.provider}
              onChange={(e) => handleProviderChange(e.target.value)}
            >
              {providers.map((provider) => (
                <option key={provider.provider} value={provider.provider}>
                  {provider.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="model-id">Model ID</Label>
            {selectedProvider && selectedProvider.models.length > 0 ? (
              <Select
                id="model-id"
                value={form.model_id}
                onChange={(e) => setField("model_id", e.target.value)}
              >
                {selectedProvider.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                id="model-id"
                value={form.model_id}
                onChange={(e) => setField("model_id", e.target.value)}
                placeholder="gpt-4o"
                required
              />
            )}
          </div>
          <div>
            <Label htmlFor="model-key">API Key Env Var</Label>
            <Input
              id="model-key"
              value={form.api_key_env_var}
              onChange={(e) => setField("api_key_env_var", e.target.value)}
              placeholder={selectedProvider?.default_api_key_env_var ?? "OPENAI_API_KEY"}
            />
            <p className="mt-1 text-xs text-gray-400">
              Store the secret in `.env`; this field saves the variable name only.
            </p>
          </div>
        </div>

        {(selectedProvider?.requires_base_url || form.base_url) && (
          <div>
            <Label htmlFor="model-url">Base URL</Label>
            <Input
              id="model-url"
              value={form.base_url}
              onChange={(e) => setField("base_url", e.target.value)}
              placeholder="http://localhost:11434"
            />
          </div>
        )}

        {(selectedProvider?.requires_api_version || form.api_version) && (
          <div>
            <Label htmlFor="model-version">API Version</Label>
            <Input
              id="model-version"
              value={form.api_version}
              onChange={(e) => setField("api_version", e.target.value)}
              placeholder="2024-10-21"
            />
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="model-max-tokens">Max Tokens</Label>
            <Input
              id="model-max-tokens"
              type="number"
              min={1}
              max={200000}
              value={form.max_tokens}
              onChange={(e) => setField("max_tokens", Number(e.target.value))}
            />
          </div>
          <div>
            <Label htmlFor="model-temperature">Temperature</Label>
            <Input
              id="model-temperature"
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={form.temperature}
              onChange={(e) => setField("temperature", Number(e.target.value))}
            />
          </div>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saving} disabled={!form.name || !form.model_id}>
            <Save size={13} /> {initialConfig ? "Save Changes" : "Create Config"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ModelSection({
  providers,
  configs,
  onReload,
  canEdit,
}: {
  providers: ProviderModelsResponse[];
  configs: ModelConfigResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfigResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(config: ModelConfigResponse) {
    setEditing(config);
    setError("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: ModelFormState) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: ModelConfigUpdate = {
        name: form.name,
        provider: form.provider,
        model_id: form.model_id,
        api_key_env_var: form.api_key_env_var || undefined,
        base_url: form.base_url || undefined,
        api_version: form.api_version || undefined,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
      };
      if (editing) {
        await updateModelConfigById(editing.id, payload);
        setNotice("Model config updated.");
      } else {
        await createModelConfig(payload);
        setNotice("Model config created.");
      }
      setModalOpen(false);
      setEditing(null);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(config: ModelConfigResponse) {
    const confirmed = window.confirm(
      `Delete model config "${config.name}"?`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteModelConfig(config.id);
      setNotice("Model config deleted.");
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleSetDefault(config: ModelConfigResponse) {
    setError("");
    setNotice("");
    try {
      await setDefaultModelConfig(config.id);
      setNotice(`"${config.name}" is now the default model.`);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Default update failed");
    }
  }

  return (
    <Section
      title="Model Manager"
      description="Saved model configs are reusable profiles for new sessions. Set one as default, or keep multiple providers ready to switch."
    >
      <div className="flex flex-wrap gap-2">
        {providers.map((provider) => (
          <span
            key={provider.provider}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
              provider.available
                ? "border-green-200 bg-green-50 text-green-700"
                : "border-gray-200 bg-gray-50 text-gray-400"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                provider.available ? "bg-green-500" : "bg-gray-300"
              }`}
            />
            {provider.label}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-600">
            {configs.length} saved config{configs.length === 1 ? "" : "s"}
          </p>
          {!canEdit && (
            <p className="text-sm text-gray-500">
              Admin role required to add or edit saved model configs.
            </p>
          )}
        </div>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> Add Model Config
        </Button>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-green-600">{notice}</p>}

      {configs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-sm text-gray-500">
          No saved model configs yet. Create one to make provider switching easier for operators.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Config</th>
                <th className="px-4 py-3">Provider</th>
                <th className="px-4 py-3">Model</th>
                <th className="px-4 py-3">Runtime</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {configs.map((config) => (
                <tr key={config.id}>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">{config.name}</span>
                      {config.is_default && <Badge>Default</Badge>}
                    </div>
                    <p className="mt-1 font-mono text-xs text-gray-400">
                      {config.api_key_env_var ?? "No API key env var"}
                    </p>
                  </td>
                  <td className="px-4 py-3 align-top capitalize text-gray-700">
                    {config.provider.replace("_", " ")}
                  </td>
                  <td className="px-4 py-3 align-top font-mono text-xs text-gray-600">
                    {config.model_id}
                  </td>
                  <td className="px-4 py-3 align-top text-gray-600">
                    <div>Max tokens: {config.max_tokens}</div>
                    <div>Temp: {config.temperature}</div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex justify-end gap-2">
                      {!config.is_default && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleSetDefault(config)}
                          disabled={!canEdit}
                        >
                          <Star size={13} /> Default
                        </Button>
                      )}
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openEditModal(config)}
                        disabled={!canEdit}
                      >
                        <Pencil size={13} /> Edit
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDelete(config)}
                        disabled={!canEdit}
                      >
                        <Trash2 size={13} /> Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ModelConfigModal
        open={modalOpen}
        onClose={closeModal}
        onSubmit={handleSubmit}
        saving={saving}
        error={error}
        providers={providers}
        initialConfig={editing}
      />
    </Section>
  );
}

// ---------------------------------------------------------------------------
// MCP Servers
// ---------------------------------------------------------------------------

type TokenMode = "keep" | "replace" | "clear";

type MCPFormState = {
  name: string;
  transport: MCPTransport;
  command: string;
  argsText: string;
  url: string;
  token: string;
  tokenMode: TokenMode;
  envText: string;
  is_active: boolean;
};

function createMCPFormState(current?: MCPServerResponse | null): MCPFormState {
  const envText = current?.env_vars
    ? Object.entries(current.env_vars)
        .map(([k, v]) => `${k}=${v}`)
        .join("\n")
    : "";
  return {
    name: current?.name ?? "",
    transport: current?.transport ?? "stdio",
    command: current?.command ?? "",
    argsText: current?.args?.join("\n") ?? "",
    url: current?.url ?? "",
    token: "",
    tokenMode: current?.has_token ? "keep" : "replace",
    envText,
    is_active: current?.is_active ?? true,
  };
}

function parseArgs(text: string): string[] | null {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length ? lines : null;
}

function parseEnv(text: string): { env: Record<string, string> | null; error?: string } {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return { env: null };
  const env: Record<string, string> = {};
  for (const line of lines) {
    const eq = line.indexOf("=");
    if (eq <= 0) return { env: null, error: `Env var line "${line}" must be KEY=value.` };
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1);
    if (!key) return { env: null, error: `Env var line "${line}" is missing a key.` };
    env[key] = value;
  }
  return { env };
}

function buildMCPPayload(
  form: MCPFormState,
): { payload?: MCPServerUpsert; error?: string } {
  if (!form.name.trim()) return { error: "Name is required." };
  if (form.transport === "stdio" && !form.command.trim()) {
    return { error: "stdio transport requires a command." };
  }
  if ((form.transport === "sse" || form.transport === "http") && !form.url.trim()) {
    return { error: `${form.transport} transport requires a URL.` };
  }

  const { env, error: envError } = parseEnv(form.envText);
  if (envError) return { error: envError };

  const payload: MCPServerUpsert = {
    name: form.name.trim(),
    transport: form.transport,
    command: form.transport === "stdio" ? form.command.trim() : null,
    args: form.transport === "stdio" ? parseArgs(form.argsText) : null,
    url: form.transport === "stdio" ? null : form.url.trim(),
    env_vars: env,
    is_active: form.is_active,
  };

  if (form.transport === "stdio") {
    payload.clear_token = true;
  } else if (form.tokenMode === "replace" && form.token.trim()) {
    payload.token = form.token;
  } else if (form.tokenMode === "clear") {
    payload.clear_token = true;
  }

  return { payload };
}

function MCPServerModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  initialServer,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: MCPFormState) => Promise<void>;
  saving: boolean;
  error: string;
  initialServer: MCPServerResponse | null;
}) {
  const [form, setForm] = useState<MCPFormState>(() =>
    createMCPFormState(initialServer),
  );

  useEffect(() => {
    if (!open) return;
    setForm(createMCPFormState(initialServer));
  }, [open, initialServer]);

  function setField<K extends keyof MCPFormState>(
    key: K,
    value: MCPFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    await onSubmit(form);
  }

  const showTokenField = form.transport !== "stdio";
  const hasExistingToken = Boolean(initialServer?.has_token);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialServer ? "Edit MCP Server" : "Add MCP Server"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="mcp-name">Name</Label>
            <Input
              id="mcp-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="kubernetes-prod"
              required
            />
          </div>
          <div>
            <Label htmlFor="mcp-transport">Transport</Label>
            <Select
              id="mcp-transport"
              value={form.transport}
              onChange={(e) => setField("transport", e.target.value as MCPTransport)}
            >
              <option value="stdio">stdio (local process)</option>
              <option value="sse">sse (server-sent events)</option>
              <option value="http">http</option>
            </Select>
          </div>
        </div>

        {form.transport === "stdio" ? (
          <>
            <div>
              <Label htmlFor="mcp-command">Command</Label>
              <Input
                id="mcp-command"
                value={form.command}
                onChange={(e) => setField("command", e.target.value)}
                placeholder="uvx"
                required
              />
            </div>
            <div>
              <Label htmlFor="mcp-args">Args (one per line)</Label>
              <Textarea
                id="mcp-args"
                rows={4}
                value={form.argsText}
                onChange={(e) => setField("argsText", e.target.value)}
                placeholder="mcp-server-kubernetes"
                className="font-mono text-xs"
              />
            </div>
          </>
        ) : (
          <div>
            <Label htmlFor="mcp-url">URL</Label>
            <Input
              id="mcp-url"
              value={form.url}
              onChange={(e) => setField("url", e.target.value)}
              placeholder="https://example.com/sse"
              required
            />
          </div>
        )}

        {showTokenField && (
          <div>
            <Label htmlFor="mcp-token">Bearer Token</Label>
            {hasExistingToken && form.tokenMode === "keep" ? (
              <div className="flex items-center gap-3 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
                <span className="font-mono tracking-widest">••••••••</span>
                <span className="text-xs text-gray-400">saved</span>
                <div className="ml-auto flex gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setField("tokenMode", "replace")}
                  >
                    Replace
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => setField("tokenMode", "clear")}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            ) : form.tokenMode === "clear" ? (
              <div className="flex items-center justify-between rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                <span>Token will be removed on save.</span>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setField("tokenMode", "keep")}
                >
                  Undo
                </Button>
              </div>
            ) : (
              <>
                <Input
                  id="mcp-token"
                  type="password"
                  value={form.token}
                  onChange={(e) => setField("token", e.target.value)}
                  placeholder={hasExistingToken ? "Enter a new token" : "Optional"}
                  autoComplete="off"
                />
                {hasExistingToken && (
                  <button
                    type="button"
                    className="mt-1 text-xs text-gray-500 hover:text-gray-700 underline-offset-2 hover:underline"
                    onClick={() => setField("tokenMode", "keep")}
                  >
                    Keep existing token
                  </button>
                )}
              </>
            )}
          </div>
        )}

        <div>
          <Label htmlFor="mcp-env">Env Vars (KEY=value, one per line)</Label>
          <Textarea
            id="mcp-env"
            rows={3}
            value={form.envText}
            onChange={(e) => setField("envText", e.target.value)}
            placeholder="KUBECONFIG=/etc/k8s/config"
            className="font-mono text-xs"
          />
        </div>

        <div>
          <label className="inline-flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setField("is_active", e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            Active (available for sessions)
          </label>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saving} disabled={!form.name.trim()}>
            <Save size={13} /> {initialServer ? "Save Changes" : "Create Server"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

type TestState = {
  status: "idle" | "running" | "success" | "failure";
  result?: MCPServerTestResponse;
};

function TestPill({ state }: { state: TestState }) {
  if (state.status === "idle") return null;
  if (state.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-600">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400" />
        Testing…
      </span>
    );
  }
  if (state.status === "success") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-green-200 bg-green-50 px-2 py-0.5 text-xs text-green-700"
        title={state.result?.tool_names.join(", ")}
      >
        <CheckCircle2 size={12} /> {state.result?.tool_count ?? 0} tool
        {state.result?.tool_count === 1 ? "" : "s"}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-700"
      title={state.result?.detail}
    >
      <XCircle size={12} /> Failed
    </span>
  );
}

function MCPSection({
  servers,
  onReload,
  canEdit,
}: {
  servers: MCPServerResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MCPServerResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(server: MCPServerResponse) {
    setEditing(server);
    setError("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: MCPFormState) {
    const { payload, error: buildError } = buildMCPPayload(form);
    if (buildError || !payload) {
      setError(buildError ?? "Invalid form values.");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      if (editing) {
        await updateMCPServer(editing.id, payload);
        setNotice("MCP server updated.");
      } else {
        await createMCPServer(payload);
        setNotice("MCP server created.");
      }
      setModalOpen(false);
      setEditing(null);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(server: MCPServerResponse) {
    const confirmed = window.confirm(`Delete MCP server "${server.name}"?`);
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteMCPServer(server.id);
      setNotice("MCP server deleted.");
      setTestStates((current) => {
        const next = { ...current };
        delete next[server.id];
        return next;
      });
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleTest(server: MCPServerResponse) {
    setTestStates((current) => ({
      ...current,
      [server.id]: { status: "running" },
    }));
    try {
      const result = await testMCPServer(server.id);
      setTestStates((current) => ({
        ...current,
        [server.id]: {
          status: result.success ? "success" : "failure",
          result,
        },
      }));
    } catch (err) {
      setTestStates((current) => ({
        ...current,
        [server.id]: {
          status: "failure",
          result: {
            success: false,
            detail: err instanceof Error ? err.message : "Request failed",
            tool_count: 0,
            tool_names: [],
          },
        },
      }));
    }
  }

  return (
    <Section
      title="MCP Servers"
      description="Saved MCP servers are resolved dynamically. New servers are immediately available to running sessions."
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-gray-600">
          {servers.length} saved server{servers.length === 1 ? "" : "s"}
        </p>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> Add MCP Server
        </Button>
      </div>

      {!canEdit && (
        <p className="text-sm text-gray-500">
          Admin role required to add, edit, or test MCP servers.
        </p>
      )}

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-green-600">{notice}</p>}

      {servers.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-sm text-gray-500">
          No MCP servers yet. Add one so agents can reach your infrastructure.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Transport</th>
                <th className="px-4 py-3">Target</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {servers.map((server) => {
                const target =
                  server.transport === "stdio"
                    ? [server.command, ...(server.args ?? [])]
                        .filter(Boolean)
                        .join(" ")
                    : server.url ?? "";
                const testState: TestState =
                  testStates[server.id] ?? { status: "idle" };
                return (
                  <tr key={server.id}>
                    <td className="px-4 py-3 align-top">
                      <div className="font-medium text-gray-900">
                        {server.name}
                      </div>
                      {server.has_token && (
                        <p className="mt-1 text-xs text-gray-400">
                          Bearer token stored
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge>{server.transport}</Badge>
                    </td>
                    <td className="px-4 py-3 align-top font-mono text-xs text-gray-600">
                      <span className="line-clamp-2 break-all">{target}</span>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-col gap-1.5">
                        <Badge variant={server.is_active ? "resolved" : "closed"}>
                          {server.is_active ? "Active" : "Inactive"}
                        </Badge>
                        <TestPill state={testState} />
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleTest(server)}
                          loading={testState.status === "running"}
                          disabled={!canEdit}
                        >
                          <Plug size={13} /> Test
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => openEditModal(server)}
                          disabled={!canEdit}
                        >
                          <Pencil size={13} /> Edit
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => handleDelete(server)}
                          disabled={!canEdit}
                        >
                          <Trash2 size={13} /> Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <MCPServerModal
        open={modalOpen}
        onClose={closeModal}
        onSubmit={handleSubmit}
        saving={saving}
        error={error}
        initialServer={editing}
      />
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Ingest Tokens (Sprint 14)
// ---------------------------------------------------------------------------

const PROVIDER_COLORS: Record<string, string> = {
  cloudwatch: "border-orange-200 bg-orange-50 text-orange-700",
  azure_monitor: "border-blue-200 bg-blue-50 text-blue-700",
  legacy_alert_vendor: "border-green-200 bg-green-50 text-green-700",
  legacy_alert_relay: "border-red-200 bg-red-50 text-red-700",
  generic: "border-gray-200 bg-gray-50 text-gray-600",
};

function ProviderBadge({ provider }: { provider: string }) {
  const label = provider.replace(/_/g, " ");
  const colors = PROVIDER_COLORS[provider] ?? PROVIDER_COLORS.generic;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${colors}`}
    >
      {label}
    </span>
  );
}

function IngestTokenSection({
  tokens,
  ingestProviders,
  onReload,
  canEdit,
}: {
  tokens: IngestTokenResponse[];
  ingestProviders: IngestProviderItem[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // One-time token reveal
  const [createdToken, setCreatedToken] = useState<IngestTokenCreatedResponse | null>(null);
  const [copied, setCopied] = useState(false);

  // Create form
  const [form, setForm] = useState<IngestTokenCreate>({
    name: "",
    provider: "generic",
  });

  function openCreateModal() {
    setForm({ name: "", provider: "generic" });
    setError("");
    setCreatedToken(null);
    setCopied(false);
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setCreatedToken(null);
    setCopied(false);
    setError("");
  }

  async function handleCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await createIngestToken(form);
      setCreatedToken(result);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleCopy() {
    if (!createdToken) return;
    try {
      await navigator.clipboard.writeText(createdToken.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for non-HTTPS
      const ta = document.createElement("textarea");
      ta.value = createdToken.token;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  async function handleRevoke(token: IngestTokenResponse) {
    const confirmed = window.confirm(
      `Revoke token "${token.name}"? External systems using this token will be rejected.`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await revokeIngestToken(token.id);
      setNotice(`Token "${token.name}" revoked.`);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revoke failed");
    }
  }

  async function handleDelete(token: IngestTokenResponse) {
    const confirmed = window.confirm(
      `Permanently delete token "${token.name}"? This cannot be undone.`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteIngestToken(token.id);
      setNotice(`Token "${token.name}" deleted.`);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  function formatLastUsed(ts: string | null): string {
    if (!ts) return "Never";
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ago`;
  }

  return (
    <Section
      title="Ingest Tokens"
      description="Manage webhook tokens for external alerting systems (CloudWatch, Azure Monitor, LegacyAlertVendor, etc.)."
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-600">
            {tokens.length} token{tokens.length === 1 ? "" : "s"}
            {tokens.filter((t) => t.is_active).length < tokens.length &&
              ` (${tokens.filter((t) => t.is_active).length} active)`}
          </p>
          {!canEdit && (
            <p className="text-sm text-gray-500">
              Admin role required to manage ingest tokens.
            </p>
          )}
        </div>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> New Token
        </Button>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-green-600">{notice}</p>}

      {tokens.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-sm text-gray-500">
          No ingest tokens yet. Create one to start receiving incidents from external monitoring tools.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Provider</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Last Used</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {tokens.map((token) => (
                <tr
                  key={token.id}
                  className={!token.is_active ? "bg-gray-50 opacity-60" : ""}
                >
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <Key size={14} className="text-gray-400" />
                      <span className="font-medium text-gray-900">
                        {token.name}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <ProviderBadge provider={token.provider} />
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Badge
                      variant={token.is_active ? "resolved" : "closed"}
                    >
                      {token.is_active ? "Active" : "Revoked"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 align-top text-gray-600">
                    {formatLastUsed(token.last_used_at)}
                  </td>
                  <td className="px-4 py-3 align-top text-gray-500 text-xs">
                    {new Date(token.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex justify-end gap-2">
                      {token.is_active && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleRevoke(token)}
                          disabled={!canEdit}
                        >
                          <ShieldOff size={13} /> Revoke
                        </Button>
                      )}
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDelete(token)}
                        disabled={!canEdit}
                      >
                        <Trash2 size={13} /> Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Token Modal */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={createdToken ? "Token Created" : "New Ingest Token"}
        maxWidth="max-w-lg"
      >
        {createdToken ? (
          <div className="space-y-4">
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-800">
                ⚠️ Copy this token now — it will never be shown again.
              </p>
            </div>
            <div>
              <Label>Token Name</Label>
              <p className="text-sm font-medium text-gray-900">
                {createdToken.name}
              </p>
            </div>
            <div>
              <Label>Provider</Label>
              <ProviderBadge provider={createdToken.provider} />
            </div>
            <div>
              <Label>Raw Token</Label>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-800 break-all select-all">
                  {createdToken.token}
                </code>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleCopy}
                >
                  <ClipboardCopy size={13} />{" "}
                  {copied ? "Copied!" : "Copy"}
                </Button>
              </div>
            </div>
            <div>
              <Label>Usage</Label>
              <code className="block rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-600">
                curl -H &quot;X-AIM-Token: {createdToken.token.slice(0, 20)}...&quot; \<br />
                &nbsp;&nbsp;-H &quot;Content-Type: application/json&quot; \<br />
                &nbsp;&nbsp;-d &apos;{`{"title":"...","description":"..."}`}&apos; \<br />
                &nbsp;&nbsp;{typeof window !== "undefined" ? window.location.origin : "http://localhost:8000"}/incidents/ingest
              </code>
            </div>
            <div className="flex justify-end">
              <Button onClick={closeModal}>Done</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <Label htmlFor="ingest-name">Token Name</Label>
              <Input
                id="ingest-name"
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                placeholder="cloudwatch-production"
                required
              />
              <p className="mt-1 text-xs text-gray-400">
                A descriptive name for this token source.
              </p>
            </div>
            <div>
              <Label htmlFor="ingest-provider">Provider Adapter</Label>
              <Select
                id="ingest-provider"
                value={form.provider}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    provider: e.target.value as IngestTokenCreate["provider"],
                  }))
                }
              >
                {ingestProviders.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </Select>
              <p className="mt-1 text-xs text-gray-400">
                Determines how inbound JSON payloads are parsed into incidents.
              </p>
            </div>

            {error && <FormError message={error} />}

            <div className="flex justify-end gap-3">
              <Button type="button" variant="secondary" onClick={closeModal}>
                Cancel
              </Button>
              <Button
                type="submit"
                loading={saving}
                disabled={!form.name.trim()}
              >
                <Key size={13} /> Create Token
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </Section>
  );
}

export default function ConfigPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";

  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [providers, setProviders] = useState<ProviderModelsResponse[]>([]);
  const [modelConfigs, setModelConfigs] = useState<ModelConfigResponse[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerResponse[]>([]);
  const [ingestTokens, setIngestTokens] = useState<IngestTokenResponse[]>([]);
  const [ingestProviderList, setIngestProviderList] = useState<IngestProviderItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadPageData = useCallback(async () => {
    const [runtimeConfig, providerList, savedConfigs, mcpList, tokenList, ipList] =
      await Promise.all([
        getConfig(),
        listProviders(),
        listModelConfigs(),
        listMCPServers(),
        listIngestTokens().catch(() => ({ items: [], total: 0 })),
        listIngestProviders().catch(() => ({ items: [] })),
      ]);
    setConfig(runtimeConfig);
    setProviders(providerList.items);
    setModelConfigs(savedConfigs.items);
    setMcpServers(mcpList.items);
    setIngestTokens(tokenList.items);
    setIngestProviderList(ipList.items);
  }, []);

  useEffect(() => {
    loadPageData().finally(() => setLoading(false));
  }, [loadPageData]);

  if (loading || !config) return <PageSpinner />;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Config</h1>
        <p className="mt-1 text-sm text-gray-500">
          Manage runtime defaults, ingest automation, saved model profiles, MCP server connections, and external ingest tokens.
        </p>
      </div>

      <TierSection config={config} onSaved={loadPageData} canEdit={canEdit} />
      <IngestAutoStartSection config={config} onSaved={loadPageData} canEdit={canEdit} />
      <ModelSection
        providers={providers}
        configs={modelConfigs}
        onReload={loadPageData}
        canEdit={canEdit}
      />
      <MCPSection
        servers={mcpServers}
        onReload={loadPageData}
        canEdit={canEdit}
      />
      <IngestTokenSection
        tokens={ingestTokens}
        ingestProviders={ingestProviderList}
        onReload={loadPageData}
        canEdit={canEdit}
      />
    </div>
  );
}
