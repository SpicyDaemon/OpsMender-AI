"use client";

import Link from "next/link";
import type { ComponentProps } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell,
  CheckCircle2,
  ClipboardCopy,
  ExternalLink,
  Eye,
  EyeOff,
  Key,
  Pencil,
  Plug,
  Plus,
  Save,
  Send,
  ShieldOff,
  Star,
  Trash2,
  Users,
  XCircle,
} from "lucide-react";
import {
  createAgentTeamProfile,
  createBotConnector,
  createIngestToken,
  getRetentionStatus,
  runRetentionNow,
  updateRetention,
  createMCPServer,
  createModelConfig,
  createWebhookTrigger,
  createWorkflowProfile,
  deleteAgentTeamProfile,
  deleteBotConnector,
  deleteIngestToken,
  deleteMCPServer,
  deleteModelConfig,
  deleteWebhookTrigger,
  deleteWorkflowProfile,
  getConfig,
  getModelBootstrapStatus,
  listAgentTeamProfiles,
  listBotConnectors,
  listBotPlatformSchemas,
  listIngestProviders,
  listMCPServerStatuses,
  listIngestTokens,
  listMCPServers,
  listModelConfigs,
  listProvidersWithParams,
  listWebhookTriggers,
  listWorkflowProfiles,
  revokeIngestToken,
  setDefaultModelConfig,
  startBotOAuth,
  startMCPOAuth,
  testMCPServer,
  testBotConnector,
  testWebhookTrigger,
  updateAgentTeamProfile,
  updateBotConnector,
  updateConfig,
  updateMCPServer,
  updateModelConfigById,
  updateWebhookTrigger,
  updateWorkflowProfile,
  listBotUserLinks,
  createBotUserLink,
  deleteBotUserLink,
  listUsers,
} from "@/lib/api";
import type {
  AgentRole,
  AgentTeamProfileResponse,
  AgentTeamProfileUpsert,
  BotConnectorCapability,
  BotConnectorFieldSchema,
  BotConnectorPlatform,
  BotConnectorPlatformSchema,
  BotConnectorResponse,
  BotConnectorStatus,
  BotConnectorTestResponse,
  BotConnectorUpsert,
  BotUserLinkResponse,
  ConfigResponse,
  IngestProviderItem,
  IngestProviderListResponse,
  IngestTokenCreate,
  IngestTokenCreatedResponse,
  IngestTokenLearnShapeResponse,
  IngestTokenListResponse,
  IngestTokenResponse,
  MCPServerResponse,
  MCPServerStatusResponse,
  MCPServerTestResponse,
  MCPServerUpsert,
  MCPTransport,
  ModelBootstrapStatusResponse,
  ModelConfigResponse,
  ModelConfigUpdate,
  ProviderModelsResponse,
  RetentionCategoryStorage,
  RetentionRunReportResponse,
  RetentionStatusResponse,
  WebhookTriggerEventType,
  WebhookTriggerFormat,
  WebhookTriggerResponse,
  WebhookTriggerTestResponse,
  WebhookTriggerUpsert,
  WorkflowNode,
  WorkflowProfileResponse,
  WorkflowProfileUpsert,
  UserResponse,
  UserListResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { StatusDot } from "@/components/ui/StatusDot";

export function ConfigCard({
  title,
  description,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm ${className}`}>
      <div className="border-b border-border-subtle bg-bg-elevated px-4 py-4 sm:px-6">
        <h3 className="text-sm font-semibold text-fg-primary">{title}</h3>
        {description && (
          <p className="mt-0.5 text-sm text-fg-secondary">{description}</p>
        )}
      </div>
      <div className="space-y-4 px-4 py-5 sm:px-6">{children}</div>
    </div>
  );
}

export function formatLastSeen(timestamp: string | null): string {
  if (!timestamp) return "Never connected successfully";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "Unknown timestamp";
  return parsed.toLocaleString();
}

export function describeMCPStatus(status: MCPServerStatusResponse | undefined): {
  tone: "green" | "amber" | "red";
  label: string;
  title: string;
} {
  if (!status) {
    return {
      tone: "red",
      label: "Unknown",
      title: "No runtime status has been recorded yet.",
    };
  }

  const lastSeen = formatLastSeen(status.last_successful_call_at);
  if (status.status === "healthy") {
    return {
      tone: "green",
      label: "Healthy",
      title: `Last successful call: ${lastSeen}`,
    };
  }
  if (status.status === "stale") {
    return {
      tone: "amber",
      label: "Stale",
      title: `Last successful call: ${lastSeen}`,
    };
  }
  return {
    tone: "red",
    label: "Error",
    title: status.last_error
      ? `Last successful call: ${lastSeen}\nError: ${status.last_error}`
      : `Last successful call: ${lastSeen}`,
  };
}

export function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-fg-primary">{title}</h3>
        {description && (
          <p className="text-sm text-fg-secondary">{description}</p>
        )}
      </div>
      {children}
    </div>
  );
}

export function ConfigPageSkeleton() {
  return (
    <div className="animate-pulse space-y-8">
      <div className="flex h-12 items-center justify-between border-b border-border-subtle bg-bg-panel px-4 sm:px-6">
        <div className="h-4 w-32 rounded bg-bg-elevated" />
      </div>
      <div className="px-4 sm:px-6">
        <div className="h-64 rounded-xl bg-bg-panel" />
      </div>
    </div>
  );
}

const LOG_LEVEL_DETAILS = {
  DEBUG:
    "Highest verbosity. Captures every node transition, every prompt, every tool argument. Useful when debugging an agent loop; can grow logs fast on busy clusters.",
  INFO:
    "Recommended default. Captures session lifecycle, tool calls, approvals, and notable runtime events without the per-step noise.",
  WARNING:
    "Drops INFO chatter; keeps anything the backend marked as a soft error (retries, degraded fallbacks, suppressed deliveries).",
  ERROR:
    "Drops WARNINGs too. Only real failures land in logs — failed tool calls, dispatcher exceptions, broken integrations.",
  CRITICAL:
    "Quietest. Only fatal events the operator must see (startup failures, irrecoverable state). Use sparingly; troubleshooting becomes harder.",
} as const;

export function TierSection({
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
            <option value="0">Tier 0 — Autonomous rollback-safe only (time-limited)</option>
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
            <option value="DEBUG">DEBUG — capture everything (loudest)</option>
            <option value="INFO">INFO — normal operation + above (default)</option>
            <option value="WARNING">WARNING — soft errors + above</option>
            <option value="ERROR">ERROR — real failures + above</option>
            <option value="CRITICAL">CRITICAL — fatal events only (quietest)</option>
          </Select>
          <p className="mt-1.5 text-xs text-fg-muted">
            {LOG_LEVEL_DETAILS[logLevel as keyof typeof LOG_LEVEL_DETAILS] ??
              "Sets the minimum severity the backend writes to stdout/the audit log."}
          </p>
        </div>
      </div>

      <div>
        <Label>Audit Output</Label>
        <p className="rounded-md bg-bg-elevated px-3 py-2 font-mono text-sm text-fg-secondary">
          {config.audit_output}
        </p>
      </div>

      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to edit runtime config.
        </p>
      )}
      {error && <FormError message={error} />}
      {success && <p className="text-sm text-status-low">Saved successfully.</p>}

      <div className="flex justify-end">
        <Button onClick={handleSave} loading={saving} disabled={!canEdit}>
          <Save size={13} /> Save
        </Button>
      </div>
    </Section>
  );
}

export function IngestAutoStartSection({
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
      <label className="flex items-start gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          disabled={!canEdit}
        />
        <div>
          <p className="text-sm font-medium text-fg-primary">
            Enable automatic session creation for ingested incidents
          </p>
          <p className="mt-1 text-sm text-fg-secondary">
            When enabled, OpsMender creates one session for a newly created incident if its severity
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
          <p className="mt-1 text-xs text-fg-muted">
            Exact provider key such as `cloudwatch`, `azure_monitor`, `gcp_monitoring`, `oci_monitoring`, `legacy_alert_vendor`, `legacy_alert_relay`, or `generic`.
            Leave blank to match any source.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-status-info-border bg-status-info-bg px-4 py-3 text-sm text-status-info">
        Auto-start only runs for newly created incidents and reuses the current runtime tier for the session.
        Duplicate ingests will not spawn extra active sessions.
      </div>

      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to edit ingest auto-start settings.
        </p>
      )}
      {error && <FormError message={error} />}
      {success && <p className="text-sm text-status-low">Saved successfully.</p>}

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
  region: string;
  profile: string;
  project: string;
  location: string;
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
    region: current?.provider_meta?.region ?? "",
    profile: current?.provider_meta?.profile ?? "",
    project: current?.provider_meta?.project ?? "",
    location: current?.provider_meta?.location ?? "",
    max_tokens: current?.max_tokens ?? 4096,
    temperature: current?.temperature ?? 0,
  };
}

function shouldUseManualModelId(
  providers: ProviderModelsResponse[],
  providerName: string,
  current?: ModelConfigResponse | null,
) {
  const provider = providers.find((item) => item.provider === providerName);
  if (!provider || provider.models.length === 0) return true;
  if (!current?.model_id) return false;
  return !provider.models.includes(current.model_id);
}

function ModelConfigModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  onErrorChange,
  providers,
  initialConfig,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: ModelFormState) => Promise<void>;
  saving: boolean;
  error: string;
  onErrorChange: (value: string) => void;
  providers: ProviderModelsResponse[];
  initialConfig: ModelConfigResponse | null;
}) {
  const [form, setForm] = useState<ModelFormState>(() =>
    createModelFormState(providers, initialConfig),
  );
  const [providerCatalogs, setProviderCatalogs] = useState(providers);
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);
  const [useManualModelId, setUseManualModelId] = useState(() =>
    shouldUseManualModelId(
      providers,
      initialConfig?.provider ?? providers[0]?.provider ?? "anthropic",
      initialConfig,
    ),
  );

  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setProviderCatalogs(providers);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setForm(createModelFormState(providers, initialConfig));
    setUseManualModelId(
      shouldUseManualModelId(
        providers,
        initialConfig?.provider ?? providers[0]?.provider ?? "anthropic",
        initialConfig,
      ),
    );
  }, [open, providers, initialConfig]);

  const selectedProvider = providerCatalogs.find(
    (provider) => provider.provider === form.provider,
  );

  function setField<K extends keyof ModelFormState>(
    key: K,
    value: ModelFormState[K],
  ) {
    if (error) onErrorChange("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handleProviderChange(providerName: string) {
    const nextProvider = providerCatalogs.find(
      (item) => item.provider === providerName,
    );
    if (error) onErrorChange("");
    setForm((current) => ({
      ...current,
      provider: providerName,
      model_id: nextProvider?.default_model_id ?? current.model_id,
      api_key_env_var: nextProvider?.default_api_key_env_var ?? "",
      base_url: "",
      api_version: "",
      region: "",
      profile: "",
      project: "",
      location: "",
    }));
    setUseManualModelId(!nextProvider || nextProvider.models.length === 0);
  }

  async function refreshProviderCatalog() {
    if (!selectedProvider) return;
    setRefreshingCatalog(true);
    if (error) onErrorChange("");
    try {
      const response = await listProvidersWithParams({
        provider: form.provider,
        model_id: form.model_id || undefined,
        api_key_env_var: form.api_key_env_var || undefined,
        base_url: form.base_url || undefined,
        api_version: form.api_version || undefined,
        region: form.region || undefined,
        profile: form.profile || undefined,
        project: form.project || undefined,
        location: form.location || undefined,
      });
      const refreshed = response.items[0];
      if (!refreshed) return;
      setProviderCatalogs((current) =>
        current.map((provider) =>
          provider.provider === refreshed.provider ? refreshed : provider,
        ),
      );
      if (refreshed.models.length > 0) {
        setUseManualModelId(false);
        setForm((current) => ({
          ...current,
          model_id: refreshed.models.includes(current.model_id)
            ? current.model_id
            : refreshed.models[0] ?? current.model_id,
        }));
      } else {
        setUseManualModelId(true);
      }
    } catch (err) {
      onErrorChange(err instanceof Error ? err.message : "Provider refresh failed");
    } finally {
      setRefreshingCatalog(false);
    }
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
            <div className="mb-1 flex items-center justify-between gap-3">
              <Label htmlFor="model-id">Model ID</Label>
              {selectedProvider && selectedProvider.models.length > 0 ? (
                <button
                  type="button"
                  className="text-xs font-medium text-fg-secondary transition-colors hover:text-fg-primary"
                  onClick={() => setUseManualModelId((current) => !current)}
                >
                  {useManualModelId ? "Use discovered suggestions" : "Type manual model ID"}
                </button>
              ) : null}
            </div>
            {selectedProvider && selectedProvider.models.length > 0 && !useManualModelId ? (
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
            <p className="mt-1 text-xs text-fg-muted">
              Provider-discovered models are suggestions only. You can always enter an explicit model or deployment ID.
            </p>
          </div>
          <div>
            <Label htmlFor="model-key">
              API Key Env Var
              {selectedProvider && !selectedProvider.requires_api_key && (
                <span className="ml-1 text-[10px] font-normal uppercase tracking-wide text-fg-muted">
                  (optional)
                </span>
              )}
            </Label>
            <Input
              id="model-key"
              value={form.api_key_env_var}
              onChange={(e) => setField("api_key_env_var", e.target.value)}
              placeholder={
                selectedProvider?.default_api_key_env_var ??
                (selectedProvider && !selectedProvider.requires_api_key
                  ? "Leave blank for keyless endpoints"
                  : "OPENAI_API_KEY")
              }
            />
            <p className="mt-1 text-xs text-fg-muted">
              {selectedProvider && !selectedProvider.requires_api_key
                ? "Store the secret in `.env`; this field saves the variable name only. Leave blank for endpoints that don't require auth (e.g. local LM Studio, vLLM)."
                : "Store the secret in `.env`; this field saves the variable name only."}
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

        {(form.provider === "bedrock" || form.region || form.profile) && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="model-region">AWS Region</Label>
              <Input
                id="model-region"
                value={form.region}
                onChange={(e) => setField("region", e.target.value)}
                placeholder="us-east-1"
              />
            </div>
            <div>
              <Label htmlFor="model-profile">AWS Profile</Label>
              <Input
                id="model-profile"
                value={form.profile}
                onChange={(e) => setField("profile", e.target.value)}
                placeholder="opsmender-prod"
              />
            </div>
          </div>
        )}

        {form.provider === "bedrock" && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
            <p>
              Bedrock discovery uses your AWS credential chain plus the selected region. Save a manual model ID, or refresh the catalog after entering region/profile.
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={refreshProviderCatalog}
              loading={refreshingCatalog}
              disabled={!form.region}
            >
              Refresh Catalog
            </Button>
          </div>
        )}

        {(form.provider === "vertex_ai" || form.project || form.location) && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="model-project">GCP Project</Label>
              <Input
                id="model-project"
                value={form.project}
                onChange={(e) => setField("project", e.target.value)}
                placeholder="my-prod-project"
              />
            </div>
            <div>
              <Label htmlFor="model-location">GCP Location</Label>
              <Input
                id="model-location"
                value={form.location}
                onChange={(e) => setField("location", e.target.value)}
                placeholder="us-central1"
              />
            </div>
          </div>
        )}

        {form.provider === "vertex_ai" && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
            <p>
              Vertex AI discovery uses ADC plus the selected project and location. Save a manual model ID, or refresh the catalog after entering both fields.
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={refreshProviderCatalog}
              loading={refreshingCatalog}
              disabled={!form.project || !form.location}
            >
              Refresh Catalog
            </Button>
          </div>
        )}

        <div className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
          Secrets are stored as environment-variable references only. Enter the variable name OpsMender should read at runtime, not the raw provider secret.
        </div>

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

export function ModelSection({
  bootstrap,
  providers,
  configs,
  onReload,
  canEdit,
}: {
  bootstrap: ModelBootstrapStatusResponse;
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
  const [warningNotice, setWarningNotice] = useState("");
  const providerById = new Map(providers.map((provider) => [provider.provider, provider]));

  function openCreateModal() {
    setEditing(null);
    setError("");
    setWarningNotice("");
    setModalOpen(true);
  }

  function openEditModal(config: ModelConfigResponse) {
    setEditing(config);
    setError("");
    setWarningNotice("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
    setWarningNotice("");
  }

  async function handleSubmit(form: ModelFormState) {
    setSaving(true);
    setError("");
    setNotice("");
    setWarningNotice("");
    try {
      const payload: ModelConfigUpdate = {
        name: form.name,
        provider: form.provider,
        model_id: form.model_id,
        api_key_env_var: form.api_key_env_var || undefined,
        base_url: form.base_url || undefined,
        api_version: form.api_version || undefined,
        provider_meta:
          form.region || form.profile || form.project || form.location
            ? {
                ...(form.region ? { region: form.region } : {}),
                ...(form.profile ? { profile: form.profile } : {}),
                ...(form.project ? { project: form.project } : {}),
                ...(form.location ? { location: form.location } : {}),
              }
            : undefined,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
      };
      const result = editing
        ? await updateModelConfigById(editing.id, payload)
        : await createModelConfig(payload);
      if (editing) {
        setNotice("Model config updated.");
      } else {
        setNotice("Model config created.");
      }
      if (result.warnings.length > 0) {
        setWarningNotice(result.warnings.map((warning) => warning.message).join(" "));
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
    setWarningNotice("");
    try {
      await deleteModelConfig(config.id);
      setNotice("Model config deleted.");
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  const modelColumns = useMemo<DataTableColumn<ModelConfigResponse>[]>(
    () => [
      {
        id: "name",
        label: "Config",
        accessor: (config) => config.name,
        sortable: true,
        searchable: true,
        cell: (config) => (
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-fg-primary">{config.name}</span>
              {config.is_default && <Badge>Default</Badge>}
            </div>
            <p className="mt-1 font-mono text-xs text-fg-muted">
              {config.api_key_env_var ?? "No API key env var"}
            </p>
          </div>
        ),
      },
      {
        id: "provider",
        label: "Provider",
        accessor: (config) => config.provider,
        sortable: true,
        searchable: true,
        filterChips: {
          options: Array.from(
            new Map(
              configs.map((c) => [
                c.provider,
                {
                  value: c.provider,
                  label:
                    providerById.get(c.provider)?.label ??
                    c.provider.replace("_", " "),
                },
              ]),
            ).values(),
          ),
          valueOf: (config) => config.provider,
        },
        cell: (config) => (
          <div className="flex items-center gap-2 capitalize">
            <StatusDot
              tone={providerById.get(config.provider)?.available ? "green" : "red"}
              title={
                providerById.get(config.provider)?.available
                  ? `${providerById.get(config.provider)?.label ?? config.provider} is available.`
                  : providerById.get(config.provider)?.error ??
                    `${providerById.get(config.provider)?.label ?? config.provider} is unavailable.`
              }
            />
            <span>{config.provider.replace("_", " ")}</span>
          </div>
        ),
      },
      {
        id: "model_id",
        label: "Model",
        accessor: (config) => config.model_id,
        sortable: true,
        searchable: true,
        cell: (config) => (
          <span className="font-mono text-xs text-fg-secondary">
            {config.model_id}
          </span>
        ),
      },
      {
        id: "runtime",
        label: "Runtime",
        accessor: (config) => config.max_tokens,
        sortable: true,
        cell: (config) => (
          <div className="text-fg-secondary">
            <div>Max tokens: {config.max_tokens}</div>
            <div>Temp: {config.temperature}</div>
          </div>
        ),
      },
      {
        id: "default_state",
        label: "State",
        accessor: (config) => (config.is_default ? "default" : "alternate"),
        sortable: true,
        filterChips: {
          options: [
            { value: "default", label: "Default" },
            { value: "alternate", label: "Alternate" },
          ],
          valueOf: (config) => (config.is_default ? "default" : "alternate"),
        },
        cell: (config) =>
          config.is_default ? (
            <Badge variant="resolved">default</Badge>
          ) : (
            <span className="text-fg-muted text-xs">alternate</span>
          ),
        hiddenByDefault: true,
      },
    ],
    [configs, providerById],
  );

  async function handleSetDefault(config: ModelConfigResponse) {
    setError("");
    setNotice("");
    setWarningNotice("");
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
      {bootstrap.needs_setup && (
        <div className="rounded-lg border border-status-medium-border bg-status-medium-bg px-4 py-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold text-status-medium">
                Default model setup is still incomplete.
              </p>
              <p className="mt-1 text-sm text-fg-secondary">
                OpsMender falls back to offline stub responses until one saved model config is marked as default. Bootstrap your first model here; secrets stay in `.env` or your deployment environment and only the env-var name is stored.
              </p>
            </div>
            <Button onClick={openCreateModal} disabled={!canEdit}>
              <Plus size={14} /> Bootstrap First Model
            </Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {providers.map((provider) => (
          <span
            key={provider.provider}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
              provider.available
                ? "border-status-low-border bg-status-low-bg text-status-low"
                : "border-border-subtle bg-bg-elevated text-fg-muted"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                provider.available ? "bg-status-low" : "bg-bg-elevated"
              }`}
            />
            {provider.label}
          </span>
        ))}
      </div>

      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to add or edit saved model configs.
        </p>
      )}

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}
      {warningNotice && <p className="text-sm text-status-medium">{warningNotice}</p>}

      <DataTable
        rows={configs}
        columns={modelColumns}
        rowKey={(config) => config.id}
        storageKey="opsmender:model-configs-table"
        searchPlaceholder="Search by config name, model, or env var…"
        toolbarRight={
          <>
            <span className="text-sm text-fg-secondary">
              {configs.length} saved config{configs.length === 1 ? "" : "s"}
            </span>
            <Button onClick={openCreateModal} disabled={!canEdit}>
              <Plus size={14} /> Add Model Config
            </Button>
          </>
        }
        empty={
          <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
            No saved model configs yet. Create one to make provider switching easier for operators.
          </div>
        }
        rowActions={(config) => (
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
        )}
      />

      <ModelConfigModal
        open={modalOpen}
        onClose={closeModal}
        onSubmit={handleSubmit}
        saving={saving}
        error={error}
        onErrorChange={setError}
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

type MCPServerTemplate = {
  id: string;
  name: string;
  description: string;
  transport: MCPTransport;
  suggestedName: string;
  command?: string;
  args?: string[];
  url?: string;
  tokenStrategy: "none" | "bearer" | "oauth";
  docsHref: string;
  docsLabel: string;
};

const MCP_SERVER_TEMPLATES: MCPServerTemplate[] = [
  {
    id: "k8s_stdio",
    name: "Kubernetes",
    description:
      "Local stdio server for cluster inspection via the Anthropic k8s MCP package.",
    transport: "stdio",
    suggestedName: "k8s-prod",
    command: "npx",
    args: ["-y", "@anthropic/mcp-server-k8s"],
    tokenStrategy: "none",
    docsHref: "https://www.npmjs.com/package/@anthropic/mcp-server-k8s",
    docsLabel: "Package docs",
  },
  {
    id: "postgres_stdio",
    name: "Postgres",
    description:
      "Local stdio bridge to a PostgreSQL database using the reference MCP server.",
    transport: "stdio",
    suggestedName: "postgres-prod",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/opsmender"],
    tokenStrategy: "none",
    docsHref: "https://www.npmjs.com/package/@modelcontextprotocol/server-postgres",
    docsLabel: "Package docs",
  },
  {
    id: "github_http",
    name: "GitHub Copilot MCP",
    description:
      "Remote HTTP MCP endpoint with OAuth. Good default for repo-aware investigation workflows.",
    transport: "http",
    suggestedName: "github",
    url: "https://api.githubcopilot.com/mcp/",
    tokenStrategy: "oauth",
    docsHref: "https://github.com/SpicyDaemon/OpsMender-AI#configuration",
    docsLabel: "OpsMender config docs",
  },
  {
    id: "http_oauth",
    name: "Generic HTTP + OAuth",
    description:
      "Remote HTTP MCP endpoint that authenticates through the Sprint 42 OAuth connect flow.",
    transport: "http",
    suggestedName: "remote-http",
    url: "https://mcp.example.com/",
    tokenStrategy: "oauth",
    docsHref: "https://github.com/SpicyDaemon/OpsMender-AI#configuration",
    docsLabel: "OpsMender config docs",
  },
  {
    id: "http_bearer",
    name: "Generic HTTP + Bearer",
    description:
      "Remote HTTP MCP endpoint with a static bearer token saved in OpsMender.",
    transport: "http",
    suggestedName: "remote-http-bearer",
    url: "https://mcp.example.com/",
    tokenStrategy: "bearer",
    docsHref: "https://github.com/SpicyDaemon/OpsMender-AI#configuration",
    docsLabel: "OpsMender config docs",
  },
  {
    id: "stdio_custom",
    name: "Custom stdio",
    description:
      "Start from a local Python or Node command when you already have a custom MCP bridge.",
    transport: "stdio",
    suggestedName: "custom-stdio",
    command: "python",
    args: ["/path/to/mcp_server.py"],
    tokenStrategy: "none",
    docsHref: "https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/skills-guide.md",
    docsLabel: "Skills guide",
  },
];

function createMCPFormStateFromTemplate(template: MCPServerTemplate): MCPFormState {
  return {
    name: template.suggestedName,
    transport: template.transport,
    command: template.command ?? "",
    argsText: template.args?.join("\n") ?? "",
    url: template.url ?? "",
    token: "",
    tokenMode: template.tokenStrategy === "bearer" ? "replace" : "keep",
    envText: "",
    is_active: true,
  };
}

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
  onSubmit: (
    form: MCPFormState,
    intent: "save" | "connect",
  ) => Promise<void>;
  saving: boolean;
  error: string;
  initialServer: MCPServerResponse | null;
}) {
  const [form, setForm] = useState<MCPFormState>(() =>
    createMCPFormState(initialServer),
  );
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setForm(createMCPFormState(initialServer));
    setSelectedTemplateId(null);
  }, [open, initialServer]);

  function setField<K extends keyof MCPFormState>(
    key: K,
    value: MCPFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const selectedTemplate = MCP_SERVER_TEMPLATES.find(
      (template) => template.id === selectedTemplateId,
    );
    const intent =
      !initialServer &&
      selectedTemplate?.tokenStrategy === "oauth" &&
      form.transport !== "stdio"
        ? "connect"
        : "save";
    await onSubmit(form, intent);
  }

  const showTokenField = form.transport !== "stdio";
  const hasExistingToken = Boolean(initialServer?.has_token);
  const selectedTemplate = MCP_SERVER_TEMPLATES.find(
    (template) => template.id === selectedTemplateId,
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialServer ? "Edit MCP Server" : "Add MCP Server"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {!initialServer && (
          <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-4">
            <div className="flex items-center gap-2">
              <Star size={14} className="text-accent" />
              <div>
                <p className="text-sm font-medium text-fg-primary">Templates</p>
                <p className="text-xs text-fg-secondary">
                  Start from a common MCP shape, then tweak the manual form before saving.
                </p>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {MCP_SERVER_TEMPLATES.map((template) => {
                const isSelected = template.id === selectedTemplateId;
                return (
                  <div
                    key={template.id}
                    className={`rounded-lg border px-3 py-3 text-left transition ${
                      isSelected
                        ? "border-accent bg-accent-bg/40"
                        : "border-border-subtle bg-bg-panel hover:border-border-strong"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-fg-primary">
                          {template.name}
                        </p>
                        <p className="mt-1 text-xs text-fg-secondary">
                          {template.description}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge>{template.transport}</Badge>
                      {template.tokenStrategy === "oauth" && (
                        <Badge variant="info">OAuth</Badge>
                      )}
                      {template.tokenStrategy === "bearer" && (
                        <Badge variant="default">Bearer token</Badge>
                      )}
                      {template.tokenStrategy === "none" && (
                        <Badge variant="resolved">No auth</Badge>
                      )}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <Link
                        href={template.docsHref}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-primary"
                      >
                        {template.docsLabel}
                        <ExternalLink size={11} aria-hidden />
                      </Link>
                      <Button
                        type="button"
                        variant={isSelected ? "primary" : "secondary"}
                        size="sm"
                        onClick={() => {
                          setSelectedTemplateId(template.id);
                          setForm(createMCPFormStateFromTemplate(template));
                        }}
                      >
                        {isSelected ? "Selected" : "Use template"}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

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
              <div className="flex items-center gap-3 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm text-fg-secondary">
                <span className="font-mono tracking-widest">••••••••</span>
                <span className="text-xs text-fg-muted">saved</span>
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
              <div className="flex items-center justify-between rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-sm text-status-critical">
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
                    className="mt-1 text-xs text-fg-secondary hover:text-fg-primary underline-offset-2 hover:underline"
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
          <label className="inline-flex items-center gap-2 text-sm text-fg-primary">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setField("is_active", e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
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
            {selectedTemplate?.tokenStrategy === "oauth" && !initialServer ? (
              <>
                <ExternalLink size={13} /> Create & Connect
              </>
            ) : (
              <>
                <Save size={13} /> {initialServer ? "Save Changes" : "Create Server"}
              </>
            )}
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
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-elevated px-2 py-0.5 text-xs text-fg-secondary">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bg-elevated" />
        Testing…
      </span>
    );
  }
  if (state.status === "success") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-status-low-border bg-status-low-bg px-2 py-0.5 text-xs text-status-low"
        title={state.result?.tool_names.join(", ")}
      >
        <CheckCircle2 size={12} /> {state.result?.tool_count ?? 0} tool
        {state.result?.tool_count === 1 ? "" : "s"}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-status-critical-border bg-status-critical-bg px-2 py-0.5 text-xs text-status-critical"
      title={state.result?.detail}
    >
      <XCircle size={12} /> Failed
    </span>
  );
}

export function MCPSection({
  servers,
  statuses,
  onReload,
  onStatusReload,
  canEdit,
}: {
  servers: MCPServerResponse[];
  statuses: MCPServerStatusResponse[];
  onReload: () => Promise<void>;
  onStatusReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MCPServerResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [oauthStartingId, setOauthStartingId] = useState<string | null>(null);
  const statusByServerId = useMemo(
    () => new Map(statuses.map((status) => [status.server_id, status])),
    [statuses],
  );

  const getServerTarget = useCallback((server: MCPServerResponse): string => {
    if (server.transport === "stdio") {
      return [server.command, ...(server.args ?? [])].filter(Boolean).join(" ");
    }
    return server.url ?? "";
  }, []);

  const getAuthState = useCallback((server: MCPServerResponse): string => {
    if (server.transport === "stdio") return "env";
    if (server.oauth_status === "connected") return "oauth_connected";
    if (server.oauth_status === "reconnect_needed") return "reconnect_needed";
    if (server.has_token) return "bearer";
    return "not_authorized";
  }, []);

  const getRuntimeState = useCallback(
    (server: MCPServerResponse): string =>
      statusByServerId.get(server.id)?.status ?? "unknown",
    [statusByServerId],
  );

  const mcpColumns = useMemo<DataTableColumn<MCPServerResponse>[]>(
    () => [
      {
        id: "name",
        label: "Server",
        accessor: (server) => server.name,
        sortable: true,
        searchable: true,
        cell: (server) => (
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-medium text-fg-primary">
              <span
                className={`h-2 w-2 rounded-full ${
                  server.is_active ? "bg-status-low" : "bg-status-neutral"
                }`}
                title={server.is_active ? "Active" : "Inactive"}
              />
              <span>{server.name}</span>
            </div>
            <p className="mt-1 text-xs text-fg-muted">
              Created {new Date(server.created_at).toLocaleDateString()}
            </p>
          </div>
        ),
      },
      {
        id: "transport",
        label: "Transport",
        accessor: (server) => server.transport,
        sortable: true,
        filterChips: {
          options: [
            { value: "stdio", label: "stdio" },
            { value: "sse", label: "SSE" },
            { value: "http", label: "HTTP" },
          ],
          valueOf: (server) => server.transport,
        },
        cell: (server) => <Badge>{server.transport}</Badge>,
      },
      {
        id: "target",
        label: "Target",
        accessor: (server) => {
          const envKeys = Object.keys(server.env_vars ?? {}).join(" ");
          return `${getServerTarget(server)} ${envKeys}`.trim();
        },
        searchable: true,
        cell: (server) => {
          const target = getServerTarget(server);
          const argsCount = server.args?.length ?? 0;
          const envCount = Object.keys(server.env_vars ?? {}).length;
          return (
            <div className="max-w-full sm:max-w-[28rem]">
              <span className="line-clamp-2 break-all font-mono text-xs text-fg-secondary">
                {target || "—"}
              </span>
              {(argsCount > 0 || envCount > 0) && (
                <p className="mt-1 text-xs text-fg-muted">
                  {argsCount > 0 ? `${argsCount} arg${argsCount === 1 ? "" : "s"}` : null}
                  {argsCount > 0 && envCount > 0 ? " · " : null}
                  {envCount > 0 ? `${envCount} env var${envCount === 1 ? "" : "s"}` : null}
                </p>
              )}
            </div>
          );
        },
      },
      {
        id: "auth",
        label: "Auth",
        accessor: (server) => getAuthState(server),
        sortable: true,
        filterChips: {
          options: [
            { value: "oauth_connected", label: "OAuth" },
            { value: "reconnect_needed", label: "Reconnect" },
            { value: "bearer", label: "Bearer" },
            { value: "not_authorized", label: "No auth" },
            { value: "env", label: "Env" },
          ],
          valueOf: (server) => getAuthState(server),
        },
        cell: (server) => {
          const authState = getAuthState(server);
          if (authState === "oauth_connected") {
            return <Badge variant="resolved">OAuth Connected</Badge>;
          }
          if (authState === "reconnect_needed") {
            return <Badge variant="high">Reconnect needed</Badge>;
          }
          if (authState === "bearer") return <Badge>Bearer</Badge>;
          if (authState === "not_authorized") {
            return <Badge variant="medium">Not authorized</Badge>;
          }
          return <Badge variant="info">Env</Badge>;
        },
      },
      {
        id: "runtime",
        label: "Runtime",
        accessor: (server) => getRuntimeState(server),
        sortable: true,
        filterChips: {
          options: [
            { value: "healthy", label: "Healthy" },
            { value: "stale", label: "Stale" },
            { value: "error", label: "Error" },
            { value: "unknown", label: "Unknown" },
          ],
          valueOf: (server) => getRuntimeState(server),
        },
        cell: (server) => {
          const runtimeStatus = describeMCPStatus(statusByServerId.get(server.id));
          const testState: TestState = testStates[server.id] ?? { status: "idle" };
          return (
            <div className="flex flex-col items-start gap-1.5">
              <StatusDot
                tone={runtimeStatus.tone}
                label={runtimeStatus.label}
                title={runtimeStatus.title}
              />
              <TestPill state={testState} />
            </div>
          );
        },
      },
      {
        id: "active",
        label: "State",
        accessor: (server) => (server.is_active ? "active" : "inactive"),
        sortable: true,
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
          ],
          valueOf: (server) => (server.is_active ? "active" : "inactive"),
        },
        cell: (server) => (
          <Badge variant={server.is_active ? "resolved" : "closed"}>
            {server.is_active ? "Active" : "Inactive"}
          </Badge>
        ),
      },
      {
        id: "created",
        label: "Created",
        accessor: (server) => server.created_at,
        sortable: true,
        hiddenByDefault: true,
        cell: (server) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {new Date(server.created_at).toLocaleString()}
          </span>
        ),
      },
    ],
    [
      getAuthState,
      getRuntimeState,
      getServerTarget,
      statusByServerId,
      testStates,
    ],
  );

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

  async function handleSubmit(
    form: MCPFormState,
    intent: "save" | "connect",
  ) {
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
        setModalOpen(false);
        setEditing(null);
        await onReload();
      } else {
        const created = await createMCPServer(payload);
        setNotice("MCP server created.");
        setModalOpen(false);
        setEditing(null);
        if (intent === "connect" && created.transport !== "stdio") {
          try {
            const { authorize_url } = await startMCPOAuth(created.id);
            window.location.assign(authorize_url);
            return;
          } catch (oauthErr) {
            setError(
              oauthErr instanceof Error
                ? `${oauthErr.message} The server was created; use Connect from the row to retry.`
                : "OAuth start failed. The server was created; use Connect from the row to retry.",
            );
          }
        }
        await onReload();
      }
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
    } finally {
      await onStatusReload();
    }
  }

  async function handleConnectOAuth(server: MCPServerResponse) {
    setOauthStartingId(server.id);
    setError("");
    setNotice("");
    try {
      const { authorize_url } = await startMCPOAuth(server.id);
      window.location.assign(authorize_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "OAuth start failed");
      setOauthStartingId(null);
    }
  }

  // Surface MCP OAuth-callback result. The backend redirects to
  // /dashboard/mcp-servers?mcp_oauth=ok|error&detail=…
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("mcp_oauth");
    if (!outcome) return;
    const detail = params.get("detail") ?? "";
    if (outcome === "ok") {
      setNotice(detail || "MCP OAuth connection complete.");
    } else {
      setError(detail || "MCP OAuth connection failed.");
    }
    params.delete("mcp_oauth");
    params.delete("detail");
    params.delete("server_id");
    const qs = params.toString();
    const url = window.location.pathname + (qs ? `?${qs}` : "");
    window.history.replaceState({}, "", url);
  }, []);

  return (
    <Section
      title="MCP Servers"
      description="Saved MCP servers are resolved dynamically. New servers are immediately available to running sessions."
    >
      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to add, edit, or test MCP servers.
        </p>
      )}

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      <DataTable
        rows={servers}
        columns={mcpColumns}
        rowKey={(server) => server.id}
        storageKey="opsmender:mcp-servers-table"
        searchPlaceholder="Search MCP servers…"
        dateRangeColumn={{
          id: "created",
          label: "Created",
          valueOf: (server) => server.created_at,
        }}
        toolbarRight={
          <>
            <span className="text-sm text-fg-secondary">
              {servers.length} saved server{servers.length === 1 ? "" : "s"}
            </span>
            <Button onClick={openCreateModal} disabled={!canEdit}>
              <Plus size={14} /> Add MCP Server
            </Button>
          </>
        }
        empty={
          <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
            No MCP servers yet. Add one so agents can reach your infrastructure.
          </div>
        }
        rowActions={(server) => {
          const testState: TestState = testStates[server.id] ?? { status: "idle" };
          return (
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => handleTest(server)}
                loading={testState.status === "running"}
                disabled={!canEdit}
              >
                <Plug size={13} /> Test
              </Button>
              {server.transport !== "stdio" && (
                <Button
                  variant={
                    server.oauth_status === "reconnect_needed"
                      ? "danger"
                      : "secondary"
                  }
                  size="sm"
                  onClick={() => handleConnectOAuth(server)}
                  loading={oauthStartingId === server.id}
                  disabled={!canEdit}
                >
                  <ExternalLink size={13} />
                  {server.oauth_status === "connected" ? "Reconnect" : "Connect"}
                </Button>
              )}
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
          );
        }}
      />

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
// Bot Connectors
// ---------------------------------------------------------------------------

const BOT_CAPABILITY_OPTIONS: Array<{
  value: BotConnectorCapability;
  label: string;
}> = [
  { value: "incident_lookup", label: "Incident lookup" },
  { value: "session_status", label: "Session status" },
  { value: "approvals", label: "Approvals" },
  { value: "copilot_chat", label: "Co-pilot chat" },
  { value: "notifications", label: "Notifications" },
];

const BOT_STATUS_VARIANTS: Record<BotConnectorStatus, ComponentProps<typeof Badge>["variant"]> = {
  not_configured: "closed",
  configured: "info",
  healthy: "resolved",
  error: "failed",
  disabled: "closed",
};

type CredentialMode = "keep" | "replace" | "clear";

type BotConnectorFormState = {
  name: string;
  platform: BotConnectorPlatform;
  configValues: Record<string, string>;
  credentialValues: Record<string, string>;
  // Advanced JSON fallback for platforms whose schema we don't have
  // (e.g. "custom", "teams"). When `schema` is null, the form falls
  // back to the legacy two-textarea UI and these strings are used.
  configText: string;
  credentialsText: string;
  credentialMode: CredentialMode;
  allowed_capabilities: BotConnectorCapability[];
  status: BotConnectorStatus;
  is_enabled: boolean;
};

function formatJson(value: Record<string, unknown> | null): string {
  if (!value || Object.keys(value).length === 0) return "";
  return JSON.stringify(value, null, 2);
}

function valuesFromConfig(
  schema: BotConnectorPlatformSchema | null,
  config: Record<string, unknown> | null,
  group: "config" | "credentials",
): Record<string, string> {
  if (!schema || !config) return {};
  const out: Record<string, string> = {};
  for (const field of schema.fields) {
    if (field.group !== group) continue;
    const raw = config[field.name];
    if (raw === undefined || raw === null) continue;
    out[field.name] = typeof raw === "string" ? raw : String(raw);
  }
  return out;
}

function createBotConnectorFormState(
  current: BotConnectorResponse | null | undefined,
  schema: BotConnectorPlatformSchema | null,
): BotConnectorFormState {
  return {
    name: current?.name ?? "",
    platform: current?.platform ?? "telegram",
    configValues: valuesFromConfig(schema, current?.config ?? null, "config"),
    credentialValues: {},
    configText: formatJson(current?.config ?? null),
    credentialsText: "",
    credentialMode: current?.has_credentials ? "keep" : "replace",
    allowed_capabilities: current?.allowed_capabilities ?? [
      "incident_lookup",
      "session_status",
      "notifications",
    ],
    status: current?.status ?? "not_configured",
    is_enabled: current?.is_enabled ?? true,
  };
}

function parseJsonObject(
  text: string,
  label: string,
): { value?: Record<string, unknown> | null; error?: string } {
  if (!text.trim()) return { value: null };
  try {
    const parsed = JSON.parse(text) as unknown;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      return { error: `${label} must be a JSON object.` };
    }
    return { value: parsed as Record<string, unknown> };
  } catch {
    return { error: `${label} must be valid JSON.` };
  }
}

function parseKeyValueSecrets(
  text: string,
): { value?: Record<string, string> | null; error?: string } {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return { value: null };
  const value: Record<string, string> = {};
  for (const line of lines) {
    const eq = line.indexOf("=");
    if (eq <= 0) {
      return { error: `Credential line "${line}" must be key=value.` };
    }
    const key = line.slice(0, eq).trim();
    const secret = line.slice(eq + 1);
    if (!key) return { error: `Credential line "${line}" is missing a key.` };
    value[key] = secret;
  }
  return { value };
}

function buildBotConnectorPayload(
  form: BotConnectorFormState,
  schema: BotConnectorPlatformSchema | null,
): { payload?: BotConnectorUpsert; error?: string } {
  if (!form.name.trim()) return { error: "Name is required." };
  if (form.allowed_capabilities.length === 0) {
    return { error: "Select at least one allowed capability." };
  }

  let configObj: Record<string, unknown> | null = null;
  let credentialsObj: Record<string, string> | null = null;

  if (schema) {
    // Schema-driven build.
    const configEntries: Record<string, unknown> = {};
    const credentialEntries: Record<string, string> = {};
    for (const field of schema.fields) {
      if (field.group === "config") {
        const value = (form.configValues[field.name] ?? "").trim();
        if (field.required && !value) {
          return { error: `${field.label} is required.` };
        }
        if (value) configEntries[field.name] = value;
      } else {
        // credentials field — only used when credentialMode === "replace"
        if (form.credentialMode === "replace") {
          const value = form.credentialValues[field.name] ?? "";
          if (field.required && !value) {
            return { error: `${field.label} is required.` };
          }
          if (value) credentialEntries[field.name] = value;
        }
      }
    }
    if (Object.keys(configEntries).length > 0) configObj = configEntries;
    if (form.credentialMode === "replace") {
      credentialsObj = credentialEntries;
    }
  } else {
    // Free-form JSON fallback for platforms without a schema (custom, teams).
    const cfg = parseJsonObject(form.configText, "Config");
    if (cfg.error) return { error: cfg.error };
    configObj = cfg.value ?? null;
    if (form.credentialMode === "replace") {
      const creds = parseKeyValueSecrets(form.credentialsText);
      if (creds.error) return { error: creds.error };
      credentialsObj = creds.value ?? null;
    }
  }

  const payload: BotConnectorUpsert = {
    name: form.name.trim(),
    platform: form.platform,
    config: configObj,
    allowed_capabilities: form.allowed_capabilities,
    status: form.status,
    is_enabled: form.is_enabled,
  };

  if (form.credentialMode === "clear") {
    payload.clear_credentials = true;
  } else if (form.credentialMode === "replace" && credentialsObj) {
    payload.credentials = credentialsObj;
  }

  return { payload };
}

function isFormFillable(
  form: BotConnectorFormState,
  schema: BotConnectorPlatformSchema | null,
): boolean {
  if (!form.name.trim()) return false;
  if (form.allowed_capabilities.length === 0) return false;
  if (!schema) return true; // free-form fallback handles its own validation
  for (const field of schema.fields) {
    if (!field.required) continue;
    if (field.group === "config") {
      if (!(form.configValues[field.name] ?? "").trim()) return false;
    } else if (form.credentialMode === "replace") {
      if (!(form.credentialValues[field.name] ?? "")) return false;
    }
  }
  return true;
}

const PLATFORM_LABELS: Record<BotConnectorPlatform, string> = {
  telegram: "Telegram",
  signal: "Signal",
  whatsapp: "WhatsApp",
  slack: "Slack",
  discord: "Discord",
  teams: "Microsoft Teams",
  mattermost: "Mattermost",
  matrix: "Matrix",
  feishu: "Lark / Feishu",
  dingtalk: "DingTalk",
  wecom: "WeCom",
  weixin: "WeChat (Official Account)",
  twilio: "Twilio (SMS/WhatsApp)",
  email: "Email (SMTP/IMAP)",
  homeassistant: "Home Assistant",
  bluebubbles: "BlueBubbles (iMessage)",
  custom: "Custom Adapter",
};

function DynamicFieldInput({
  field,
  value,
  onChange,
  showSecret,
  onToggleSecret,
}: {
  field: BotConnectorFieldSchema;
  value: string;
  onChange: (next: string) => void;
  showSecret: boolean;
  onToggleSecret: () => void;
}) {
  const inputId = `bot-field-${field.group}-${field.name}`;
  const isSecret = field.kind === "secret";
  const inputType = isSecret && !showSecret ? "password" : "text";
  const common = {
    id: inputId,
    value,
    placeholder: field.placeholder ?? undefined,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      onChange(e.target.value),
  };
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={inputId}>
          {field.label}
          {field.required && <span className="ml-0.5 text-status-critical">*</span>}
        </Label>
        {field.doc_url && (
          <a
            href={field.doc_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-fg-secondary underline-offset-2 hover:text-fg-primary hover:underline"
          >
            Where do I get this? <ExternalLink size={11} />
          </a>
        )}
      </div>
      {field.kind === "textarea" ? (
        <Textarea {...common} rows={3} className="font-mono text-xs" />
      ) : field.kind === "select" ? (
        <Select {...common}>
          {!field.required && <option value="">(unset)</option>}
          {field.options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      ) : isSecret ? (
        <div className="relative">
          <Input {...common} type={inputType} autoComplete="off" />
          <button
            type="button"
            onClick={onToggleSecret}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-fg-secondary hover:text-fg-primary"
            aria-label={showSecret ? "Hide value" : "Show value"}
          >
            {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      ) : (
        <Input {...common} type={field.kind === "url" ? "url" : "text"} />
      )}
      {field.helper && (
        <p className="mt-1 text-xs text-fg-secondary">{field.helper}</p>
      )}
    </div>
  );
}

function DynamicConnectorForm({
  schema,
  form,
  setForm,
  initialConnector,
}: {
  schema: BotConnectorPlatformSchema;
  form: BotConnectorFormState;
  setForm: React.Dispatch<React.SetStateAction<BotConnectorFormState>>;
  initialConnector: BotConnectorResponse | null;
}) {
  const [shownSecrets, setShownSecrets] = useState<Record<string, boolean>>({});

  const configFields = schema.fields.filter((f) => f.group === "config");
  const credentialFields = schema.fields.filter((f) => f.group === "credentials");
  const hasExistingCredentials = Boolean(initialConnector?.has_credentials);

  function setConfigValue(name: string, value: string) {
    setForm((current) => ({
      ...current,
      configValues: { ...current.configValues, [name]: value },
    }));
  }
  function setCredentialValue(name: string, value: string) {
    setForm((current) => ({
      ...current,
      credentialValues: { ...current.credentialValues, [name]: value },
    }));
  }
  function toggleSecret(name: string) {
    setShownSecrets((s) => ({ ...s, [name]: !s[name] }));
  }
  function setCredentialMode(mode: CredentialMode) {
    setForm((current) => ({ ...current, credentialMode: mode }));
  }

  return (
    <div className="space-y-5">
      {configFields.length > 0 && (
        <fieldset className="space-y-3">
          <legend className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Configuration
          </legend>
          {configFields.map((field) => (
            <DynamicFieldInput
              key={field.name}
              field={field}
              value={form.configValues[field.name] ?? ""}
              onChange={(v) => setConfigValue(field.name, v)}
              showSecret={Boolean(shownSecrets[field.name])}
              onToggleSecret={() => toggleSecret(field.name)}
            />
          ))}
        </fieldset>
      )}

      {credentialFields.length > 0 && (
        <fieldset className="space-y-3">
          <legend className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            Credentials
          </legend>
          {hasExistingCredentials && form.credentialMode === "keep" ? (
            <div className="flex items-center gap-3 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm text-fg-secondary">
              <span className="font-mono tracking-widest">********</span>
              <span className="text-xs text-fg-muted">
                saved keys: {initialConnector?.credential_keys.join(", ")}
              </span>
              <div className="ml-auto flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setCredentialMode("replace")}
                >
                  Replace
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  size="sm"
                  onClick={() => setCredentialMode("clear")}
                >
                  Remove
                </Button>
              </div>
            </div>
          ) : form.credentialMode === "clear" ? (
            <div className="flex items-center justify-between rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-sm text-status-critical">
              <span>Credentials will be removed on save.</span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setCredentialMode("keep")}
              >
                Undo
              </Button>
            </div>
          ) : (
            <>
              {credentialFields.map((field) => (
                <DynamicFieldInput
                  key={field.name}
                  field={field}
                  value={form.credentialValues[field.name] ?? ""}
                  onChange={(v) => setCredentialValue(field.name, v)}
                  showSecret={Boolean(shownSecrets[field.name])}
                  onToggleSecret={() => toggleSecret(field.name)}
                />
              ))}
              {hasExistingCredentials && (
                <button
                  type="button"
                  className="text-xs text-fg-secondary underline-offset-2 hover:text-fg-primary hover:underline"
                  onClick={() => setCredentialMode("keep")}
                >
                  Keep existing credentials instead
                </button>
              )}
            </>
          )}
        </fieldset>
      )}
    </div>
  );
}

function BotConnectorModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  initialConnector,
  schemas,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: BotConnectorFormState) => Promise<void>;
  saving: boolean;
  error: string;
  initialConnector: BotConnectorResponse | null;
  schemas: Record<string, BotConnectorPlatformSchema>;
}) {
  const initialSchema = initialConnector
    ? schemas[initialConnector.platform] ?? null
    : schemas["telegram"] ?? null;

  const [form, setForm] = useState<BotConnectorFormState>(() =>
    createBotConnectorFormState(initialConnector, initialSchema),
  );
  const [testState, setTestState] = useState<{
    status: "idle" | "running" | "success" | "failure";
    result?: BotConnectorTestResponse;
  }>({ status: "idle" });

  useEffect(() => {
    if (!open) return;
    const schema = initialConnector
      ? schemas[initialConnector.platform] ?? null
      : schemas["telegram"] ?? null;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setForm(createBotConnectorFormState(initialConnector, schema));
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTestState({ status: "idle" });
  }, [open, initialConnector, schemas]);

  const schema = schemas[form.platform] ?? null;

  function setField<K extends keyof BotConnectorFormState>(
    key: K,
    value: BotConnectorFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handlePlatformChange(next: BotConnectorPlatform) {
    setForm((current) => {
      const nextSchema = schemas[next] ?? null;
      const sameAsInitial = initialConnector && initialConnector.platform === next;
      return {
        ...current,
        platform: next,
        configValues: sameAsInitial
          ? valuesFromConfig(nextSchema, initialConnector?.config ?? null, "config")
          : {},
        credentialValues: {},
      };
    });
  }

  function toggleCapability(capability: BotConnectorCapability) {
    setForm((current) => {
      const hasCapability = current.allowed_capabilities.includes(capability);
      return {
        ...current,
        allowed_capabilities: hasCapability
          ? current.allowed_capabilities.filter((item) => item !== capability)
          : [...current.allowed_capabilities, capability],
      };
    });
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    await onSubmit(form);
  }

  async function handleTestConnection() {
    if (!initialConnector) return;
    setTestState({ status: "running" });
    try {
      const result = await testBotConnector(initialConnector.id);
      setTestState({
        status: result.success ? "success" : "failure",
        result,
      });
    } catch (err) {
      setTestState({
        status: "failure",
        result: {
          success: false,
          detail: err instanceof Error ? err.message : "Request failed",
          status: "error",
        },
      });
    }
  }

  const [oauthStarting, setOauthStarting] = useState(false);

  async function handleConnectOAuth() {
    if (!initialConnector) return;
    setOauthStarting(true);
    try {
      const { authorize_url } = await startBotOAuth(
        initialConnector.platform,
        initialConnector.id,
      );
      window.location.assign(authorize_url);
    } catch (err) {
      setTestState({
        status: "failure",
        result: {
          success: false,
          detail: err instanceof Error ? err.message : "Unable to start OAuth.",
          status: "error",
        },
      });
      setOauthStarting(false);
    }
  }

  const fillable = isFormFillable(form, schema);
  const oauthEnabled = Boolean(initialConnector && schema?.oauth_enabled);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialConnector ? "Edit Bot Connector" : "Add Bot Connector"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="bot-name">Name</Label>
            <Input
              id="bot-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="telegram-ops"
              required
            />
          </div>
          <div>
            <Label htmlFor="bot-platform">Platform</Label>
            <Select
              id="bot-platform"
              value={form.platform}
              onChange={(e) => handlePlatformChange(e.target.value as BotConnectorPlatform)}
            >
              {(Object.keys(PLATFORM_LABELS) as BotConnectorPlatform[]).map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABELS[p]}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {schema ? (
          <DynamicConnectorForm
            schema={schema}
            form={form}
            setForm={setForm}
            initialConnector={initialConnector}
          />
        ) : (
          <>
            <div className="rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-xs text-fg-secondary">
              No typed schema is registered for this platform. Use the raw JSON
              fields below to provide configuration and credentials.
            </div>
            <div>
              <Label htmlFor="bot-config">Config JSON</Label>
              <Textarea
                id="bot-config"
                rows={5}
                value={form.configText}
                onChange={(e) => setField("configText", e.target.value)}
                placeholder={'{\n  "default_chat_id": "-100123"\n}'}
                className="font-mono text-xs"
              />
            </div>
            <div>
              <Label htmlFor="bot-credentials">Credentials (key=value, one per line)</Label>
              {Boolean(initialConnector?.has_credentials) && form.credentialMode === "keep" ? (
                <div className="flex items-center gap-3 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm text-fg-secondary">
                  <span className="font-mono tracking-widest">********</span>
                  <span className="text-xs text-fg-muted">
                    saved keys: {initialConnector?.credential_keys.join(", ")}
                  </span>
                  <div className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => setField("credentialMode", "replace")}
                    >
                      Replace
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      onClick={() => setField("credentialMode", "clear")}
                    >
                      Remove
                    </Button>
                  </div>
                </div>
              ) : form.credentialMode === "clear" ? (
                <div className="flex items-center justify-between rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-sm text-status-critical">
                  <span>Credentials will be removed on save.</span>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setField("credentialMode", "keep")}
                  >
                    Undo
                  </Button>
                </div>
              ) : (
                <Textarea
                  id="bot-credentials"
                  rows={4}
                  value={form.credentialsText}
                  onChange={(e) => setField("credentialsText", e.target.value)}
                  placeholder="bot_token=..."
                  className="font-mono text-xs"
                />
              )}
            </div>
          </>
        )}

        <div>
          <Label>Allowed Capabilities</Label>
          <div className="grid gap-2 md:grid-cols-2">
            {BOT_CAPABILITY_OPTIONS.map((option) => (
              <label
                key={option.value}
                className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-sm text-fg-primary"
              >
                <input
                  type="checkbox"
                  checked={form.allowed_capabilities.includes(option.value)}
                  onChange={() => toggleCapability(option.value)}
                  className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
                />
                {option.label}
              </label>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="bot-status">Status</Label>
            <Select
              id="bot-status"
              value={form.status}
              onChange={(e) => setField("status", e.target.value as BotConnectorStatus)}
            >
              <option value="not_configured">Not configured</option>
              <option value="configured">Configured</option>
              <option value="healthy">Healthy</option>
              <option value="error">Error</option>
              <option value="disabled">Disabled</option>
            </Select>
          </div>
          <div className="flex items-end">
            <label className="inline-flex items-center gap-2 text-sm text-fg-primary">
              <input
                type="checkbox"
                checked={form.is_enabled}
                onChange={(e) => setField("is_enabled", e.target.checked)}
                className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
              />
              Enabled for chat workflows
            </label>
          </div>
        </div>

        {testState.status !== "idle" && testState.result && (
          <div
            className={
              testState.status === "success"
                ? "rounded-md border border-status-low-border bg-status-low-bg px-3 py-2 text-sm text-status-low"
                : "rounded-md border border-status-critical-border bg-status-critical-bg px-3 py-2 text-sm text-status-critical"
            }
          >
            <div className="flex items-center gap-2">
              {testState.status === "success" ? (
                <CheckCircle2 size={14} />
              ) : (
                <XCircle size={14} />
              )}
              <span className="font-medium">
                {testState.status === "success" ? "Test passed" : "Test failed"}
              </span>
              <Badge>{testState.result.status.replace(/_/g, " ")}</Badge>
            </div>
            <p className="mt-1 text-xs">{testState.result.detail}</p>
          </div>
        )}

        {error && <FormError message={error} />}

        {oauthEnabled && (
          <div className="rounded-md border border-border-subtle bg-bg-elevated px-3 py-3 text-sm text-fg-secondary">
            <p className="font-medium text-fg-primary">
              Install via OAuth instead of pasting tokens
            </p>
            <p className="mt-1 text-xs">
              Authorize {PLATFORM_LABELS[form.platform]} to populate the bot
              token automatically. You may still need to paste the webhook
              verification secret manually after the install completes.
            </p>
          </div>
        )}

        {error && <FormError message={error} />}

        <div className="flex flex-wrap justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          {oauthEnabled && (
            <Button
              type="button"
              variant="secondary"
              onClick={handleConnectOAuth}
              loading={oauthStarting}
            >
              <ExternalLink size={13} /> Connect to {PLATFORM_LABELS[form.platform]}
            </Button>
          )}
          {initialConnector && (
            <Button
              type="button"
              variant="secondary"
              onClick={handleTestConnection}
              loading={testState.status === "running"}
            >
              <Plug size={13} /> Test connection
            </Button>
          )}
          <Button type="submit" loading={saving} disabled={!fillable}>
            <Save size={13} /> {initialConnector ? "Save Changes" : "Create Connector"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function BotUserLinksModal({
  open,
  onClose,
  connector,
}: {
  open: boolean;
  onClose: () => void;
  connector: BotConnectorResponse | null;
}) {
  const [links, setLinks] = useState<BotUserLinkResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  
  // Form state for new link
  const [platformUserId, setPlatformUserId] = useState("");
  const [opsMenderUserId, setOpsMenderUserId] = useState("");
  const [creating, setCreating] = useState(false);

  const loadData = useCallback(async () => {
    if (!connector) return;
    setLoading(true);
    setError("");
    try {
      const [linksRes, usersRes] = await Promise.all([
        listBotUserLinks(connector.id),
        listUsers(),
      ]);
      setLinks(linksRes.items);
      setUsers(usersRes.items.filter(u => u.is_active));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load links");
    } finally {
      setLoading(false);
    }
  }, [connector]);

  useEffect(() => {
    if (open && connector) {
      loadData();
      setPlatformUserId("");
      setOpsMenderUserId("");
    }
  }, [open, connector, loadData]);

  async function handleAddLink(e: React.FormEvent) {
    e.preventDefault();
    if (!connector || !platformUserId.trim() || !opsMenderUserId) return;
    
    setCreating(true);
    setError("");
    try {
      await createBotUserLink(connector.id, {
        platform_user_id: platformUserId.trim(),
        opsmender_user_id: opsMenderUserId,
      });
      setPlatformUserId("");
      setOpsMenderUserId("");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create link");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteLink(link: BotUserLinkResponse) {
    if (!connector) return;
    if (!window.confirm(`Remove link for platform user "${link.platform_user_id}"?`)) return;
    
    setError("");
    try {
      await deleteBotUserLink(connector.id, link.id);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete link");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={connector ? `Identity Links: ${connector.name}` : "Bot Identity Links"}
      maxWidth="max-w-2xl"
    >
      <div className="space-y-6">
        <p className="text-sm text-fg-secondary">
          Map platform-specific user IDs (Telegram usernames, WhatsApp numbers, Signal IDs)
          to OpsMender users to enable permissions and audit trails for bot interactions.
        </p>

        {error && <FormError message={error} />}

        <form onSubmit={handleAddLink} className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-fg-muted">Link New Identity</h4>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="platform-user-id">Platform User ID</Label>
              <Input
                id="platform-user-id"
                value={platformUserId}
                onChange={(e) => setPlatformUserId(e.target.value)}
                placeholder={connector?.platform === "whatsapp" ? "1234567890" : "@username"}
                required
              />
            </div>
            <div>
              <Label htmlFor="opsmender-user-id">OpsMender User</Label>
              <Select
                id="opsmender-user-id"
                value={opsMenderUserId}
                onChange={(e) => setOpsMenderUserId(e.target.value)}
                required
              >
                <option value="">Select a user...</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username} ({u.role})
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="mt-4 flex justify-end">
            <Button type="submit" size="sm" loading={creating} disabled={!platformUserId.trim() || !opsMenderUserId}>
              <Plus size={14} /> Add Link
            </Button>
          </div>
        </form>

        <div className="space-y-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-fg-muted">Active Mappings ({links.length})</h4>
          {loading && links.length === 0 ? (
            <div className="py-8 text-center text-sm text-fg-muted animate-pulse">Loading links...</div>
          ) : links.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-subtle py-8 text-center text-sm text-fg-muted">
              No identity links configured yet.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border-subtle">
              <table className="min-w-full divide-y divide-border-subtle text-sm">
                <thead className="bg-bg-elevated text-left text-xs font-semibold text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2">Platform ID</th>
                    <th className="px-4 py-2">OpsMender User</th>
                    <th className="px-4 py-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle bg-bg-panel">
                  {links.map((link) => (
                    <tr key={link.id}>
                      <td className="px-4 py-2 font-mono text-xs text-fg-primary">
                        {link.platform_user_id}
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-col">
                          <span className="font-medium text-fg-primary">{link.opsmender_username}</span>
                          <span className="text-[10px] uppercase text-fg-muted">{link.opsmender_role}</span>
                        </div>
                      </td>
                      <td className="px-4 py-2 text-right">
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => handleDeleteLink(link)}
                        >
                          <Trash2 size={12} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>Close</Button>
        </div>
      </div>
    </Modal>
  );
}

type BotConnectorTestState = {
  status: "idle" | "running" | "success" | "failure";
  result?: BotConnectorTestResponse;
};

function BotConnectorTestPill({ state }: { state: BotConnectorTestState }) {
  if (state.status === "idle") return null;
  if (state.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-elevated px-2 py-0.5 text-xs text-fg-secondary">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
        Testing...
      </span>
    );
  }
  if (state.status === "success") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-status-low-border bg-status-low-bg px-2 py-0.5 text-xs text-status-low"
        title={state.result?.detail}
      >
        <CheckCircle2 size={12} /> Ready
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-status-critical-border bg-status-critical-bg px-2 py-0.5 text-xs text-status-critical"
      title={state.result?.detail}
    >
      <XCircle size={12} /> Failed
    </span>
  );
}

export function BotConnectorSection({
  connectors,
  onReload,
  canEdit,
}: {
  connectors: BotConnectorResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<BotConnectorResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testStates, setTestStates] = useState<Record<string, BotConnectorTestState>>({});

  const [linksModalOpen, setLinksModalOpen] = useState(false);
  const [linkingConnector, setLinkingConnector] = useState<BotConnectorResponse | null>(null);

  const [schemas, setSchemas] = useState<Record<string, BotConnectorPlatformSchema>>({});

  useEffect(() => {
    let cancelled = false;
    listBotPlatformSchemas()
      .then((resp) => {
        if (cancelled) return;
        const map: Record<string, BotConnectorPlatformSchema> = {};
        for (const item of resp.items) map[item.platform] = item;
        setSchemas(map);
      })
      .catch(() => {
        // Schema endpoint is optional; the form falls back to raw JSON.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Surface OAuth-callback result. The backend redirects to
  // /dashboard/bot-connectors?bot_oauth=ok|error&detail=…
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("bot_oauth");
    if (!outcome) return;
    const detail = params.get("detail") ?? "";
    if (outcome === "ok") {
      setNotice(detail || "OAuth install complete.");
    } else {
      setError(detail || "OAuth install failed.");
    }
    // Strip the query so a refresh doesn't re-show the banner.
    params.delete("bot_oauth");
    params.delete("detail");
    params.delete("platform");
    params.delete("connector_id");
    const qs = params.toString();
    const url = window.location.pathname + (qs ? `?${qs}` : "");
    window.history.replaceState({}, "", url);
  }, []);

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(connector: BotConnectorResponse) {
    setEditing(connector);
    setError("");
    setModalOpen(true);
  }

  function openLinksModal(connector: BotConnectorResponse) {
    setLinkingConnector(connector);
    setLinksModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: BotConnectorFormState) {
    const schema = schemas[form.platform] ?? null;
    const { payload, error: buildError } = buildBotConnectorPayload(form, schema);
    if (buildError || !payload) {
      setError(buildError ?? "Invalid form values.");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      if (editing) {
        await updateBotConnector(editing.id, payload);
        setNotice("Bot connector updated.");
      } else {
        await createBotConnector(payload);
        setNotice("Bot connector created.");
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

  async function handleDelete(connector: BotConnectorResponse) {
    const confirmed = window.confirm(`Delete bot connector "${connector.name}"?`);
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteBotConnector(connector.id);
      setNotice("Bot connector deleted.");
      setTestStates((current) => {
        const next = { ...current };
        delete next[connector.id];
        return next;
      });
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleTest(connector: BotConnectorResponse) {
    setTestStates((current) => ({
      ...current,
      [connector.id]: { status: "running" },
    }));
    try {
      const result = await testBotConnector(connector.id);
      setTestStates((current) => ({
        ...current,
        [connector.id]: {
          status: result.success ? "success" : "failure",
          result,
        },
      }));
      await onReload();
    } catch (err) {
      setTestStates((current) => ({
        ...current,
        [connector.id]: {
          status: "failure",
          result: {
            success: false,
            detail: err instanceof Error ? err.message : "Request failed",
            status: "error",
          },
        },
      }));
    }
  }

  return (
    <Section
      title="Chat Bot Connectors"
      description="Configure external chat channels for incident lookup, session status, approvals, co-pilot relay, and notifications."
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-fg-secondary">
            {connectors.length} saved connector{connectors.length === 1 ? "" : "s"}
          </p>
          {!canEdit && (
            <p className="text-sm text-fg-secondary">
              Admin role required to manage chat bot connectors.
            </p>
          )}
        </div>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> Add Connector
        </Button>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      {connectors.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
          No chat bot connectors yet. Add one to prepare Telegram, Signal, WhatsApp, or custom chat surfaces.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border-subtle">
          <table className="min-w-full divide-y divide-border-subtle text-sm">
            <thead className="bg-bg-elevated text-left text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              <tr>
                <th className="px-4 py-3">Connector</th>
                <th className="px-4 py-3">Capabilities</th>
                <th className="px-4 py-3">Credentials</th>
                <th className="px-4 py-3">Health</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle bg-bg-panel">
              {connectors.map((connector) => {
                const testState = testStates[connector.id] ?? { status: "idle" };
                return (
                  <tr
                    key={connector.id}
                    className={!connector.is_enabled ? "bg-bg-elevated opacity-70" : ""}
                  >
                    <td className="px-4 py-3 align-top">
                      <div className="flex items-center gap-2">
                        <Plug size={14} className="text-fg-muted" />
                        <span className="font-medium text-fg-primary">
                          {connector.name}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Badge>{connector.platform}</Badge>
                        <Badge variant={connector.is_enabled ? "resolved" : "closed"}>
                          {connector.is_enabled ? "Enabled" : "Disabled"}
                        </Badge>
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex max-w-xs flex-wrap gap-1.5">
                        {connector.allowed_capabilities.map((capability) => (
                          <Badge key={capability}>
                            {capability.replace(/_/g, " ")}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      {connector.has_credentials ? (
                        <p className="font-mono text-xs text-fg-secondary">
                          {connector.credential_keys.join(", ")}
                        </p>
                      ) : (
                        <p className="text-xs text-fg-muted">No credentials stored.</p>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-col gap-1.5">
                        <Badge variant={BOT_STATUS_VARIANTS[connector.status]}>
                          {connector.status.replace(/_/g, " ")}
                        </Badge>
                        <BotConnectorTestPill state={testState} />
                        <p className="text-xs text-fg-muted">
                          Last checked: {formatRelativeTimestamp(connector.last_checked_at)}
                        </p>
                        {connector.last_error && (
                          <p
                            className="line-clamp-2 text-xs text-status-critical"
                            title={connector.last_error}
                          >
                            {connector.last_error}
                          </p>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => openLinksModal(connector)}
                          title="Manage user identity links"
                        >
                          <Users size={13} /> Links
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleTest(connector)}
                          loading={testState.status === "running"}
                          disabled={!canEdit}
                        >
                          <Plug size={13} /> Test
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => openEditModal(connector)}
                          disabled={!canEdit}
                        >
                          <Pencil size={13} /> Edit
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => handleDelete(connector)}
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

      <BotConnectorModal
        open={modalOpen}
        onClose={closeModal}
        onSubmit={handleSubmit}
        saving={saving}
        error={error}
        initialConnector={editing}
        schemas={schemas}
      />

      <BotUserLinksModal
        open={linksModalOpen}
        onClose={() => setLinksModalOpen(false)}
        connector={linkingConnector}
      />
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Ingest Tokens (Sprint 14)
// ---------------------------------------------------------------------------

const PROVIDER_COLORS: Record<string, string> = {
  auto: "border-accent bg-accent-bg text-accent",
  cloudwatch: "border-status-high-border bg-status-high-bg text-status-high",
  azure_monitor: "border-status-info-border bg-status-info-bg text-status-info",
  legacy_alert_vendor: "border-status-low-border bg-status-low-bg text-status-low",
  legacy_alert_relay: "border-status-critical-border bg-status-critical-bg text-status-critical",
  generic: "border-border-subtle bg-bg-elevated text-fg-secondary",
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

export function IngestTokenSection({
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
    provider: "auto",
  });
  const [sampleText, setSampleText] = useState("");
  const [sampleError, setSampleError] = useState("");

  function openCreateModal() {
    setForm({ name: "", provider: "auto" });
    setSampleText("");
    setSampleError("");
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
    setSampleError("");

    let samplePayload: Record<string, unknown> | null = null;
    if (form.provider === "auto" && sampleText.trim()) {
      try {
        const parsed = JSON.parse(sampleText);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setSampleError("Sample payload must be a JSON object.");
          setSaving(false);
          return;
        }
        samplePayload = parsed as Record<string, unknown>;
      } catch {
        setSampleError("Sample payload is not valid JSON.");
        setSaving(false);
        return;
      }
    }

    try {
      const result = await createIngestToken({
        ...form,
        sample_payload: samplePayload,
      });
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

  const providerOptions = useMemo(
    () =>
      Array.from(
        new Map(
          ingestProviders.map((p) => [p.key, { value: p.key, label: p.label }]),
        ).values(),
      ),
    [ingestProviders],
  );

  const tokenColumns = useMemo<DataTableColumn<IngestTokenResponse>[]>(
    () => [
      {
        id: "name",
        label: "Name",
        accessor: (token) => token.name,
        sortable: true,
        searchable: true,
        cell: (token) => (
          <div className="flex items-center gap-2">
            <Key size={14} className="text-fg-muted" />
            <span className="font-medium text-fg-primary">{token.name}</span>
          </div>
        ),
      },
      {
        id: "provider",
        label: "Provider",
        accessor: (token) => token.provider,
        sortable: true,
        searchable: true,
        filterChips: {
          options: providerOptions,
          valueOf: (token) => token.provider,
        },
        cell: (token) => <ProviderBadge provider={token.provider} />,
      },
      {
        id: "status",
        label: "Status",
        accessor: (token) => (token.is_active ? "active" : "revoked"),
        sortable: true,
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "revoked", label: "Revoked" },
          ],
          valueOf: (token) => (token.is_active ? "active" : "revoked"),
        },
        cell: (token) => (
          <Badge variant={token.is_active ? "resolved" : "closed"}>
            {token.is_active ? "Active" : "Revoked"}
          </Badge>
        ),
      },
      {
        id: "shapes",
        label: "Shapes",
        accessor: (token) =>
          token.provider === "auto" ? token.shape_cache_size : -1,
        sortable: true,
        align: "right",
        cell: (token) =>
          token.provider === "auto" ? (
            <span
              className="text-xs text-fg-secondary"
              title="Unique payload shapes this token has learned"
            >
              {token.shape_cache_size} learned
            </span>
          ) : (
            <span className="text-xs text-fg-muted">—</span>
          ),
      },
      {
        id: "last_used",
        label: "Last Used",
        accessor: (token) => token.last_used_at ?? "",
        sortable: true,
        cell: (token) => (
          <span className="text-fg-secondary">
            {formatLastUsed(token.last_used_at)}
          </span>
        ),
      },
      {
        id: "created_at",
        label: "Created",
        accessor: (token) => token.created_at,
        sortable: true,
        cell: (token) => (
          <span className="text-xs text-fg-secondary">
            {new Date(token.created_at).toLocaleDateString()}
          </span>
        ),
      },
    ],
    [providerOptions],
  );

  return (
    <Section
      title="Ingest Tokens"
      description="Manage webhook tokens for external alerting systems (CloudWatch, Azure Monitor, GCP Monitoring, OCI, LegacyAlertVendor, etc.)."
    >
      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to manage ingest tokens.
        </p>
      )}

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      <DataTable
        rows={tokens}
        columns={tokenColumns}
        rowKey={(token) => token.id}
        storageKey="opsmender:ingest-tokens-table"
        searchPlaceholder="Search by token name or provider…"
        dateRangeColumn={{
          id: "created_at",
          label: "Created",
          valueOf: (token) => token.created_at,
        }}
        toolbarRight={
          <>
            <span className="text-sm text-fg-secondary">
              {tokens.length} token{tokens.length === 1 ? "" : "s"}
              {tokens.filter((t) => t.is_active).length < tokens.length &&
                ` (${tokens.filter((t) => t.is_active).length} active)`}
            </span>
            <Button onClick={openCreateModal} disabled={!canEdit}>
              <Plus size={14} /> New Token
            </Button>
          </>
        }
        empty={
          <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
            No ingest tokens yet. Create one to start receiving incidents from external monitoring tools.
          </div>
        }
        rowActions={(token) => (
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
        )}
      />

      {/* Create Token Modal */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={createdToken ? "Token Created" : "New Ingest Token"}
        maxWidth="max-w-lg"
      >
        {createdToken ? (
          <div className="space-y-4">
            <div className="rounded-lg border border-status-high-border bg-status-high-bg p-4">
              <p className="text-sm font-medium text-status-high">
                ⚠️ Copy this token now — it will never be shown again.
              </p>
            </div>
            <div>
              <Label>Token Name</Label>
              <p className="text-sm font-medium text-fg-primary">
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
                <code className="flex-1 rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 font-mono text-xs text-fg-primary break-all select-all">
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
              <code className="block rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 font-mono text-xs text-fg-secondary">
                curl -H &quot;X-OpsMender-Token: {createdToken.token.slice(0, 20)}...&quot; \<br />
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
              <p className="mt-1 text-xs text-fg-muted">
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
              <p className="mt-1 text-xs text-fg-muted">
                Determines how inbound JSON payloads are parsed into incidents.
                Use <strong>Auto-detect</strong> for any webhook — the token
                learns the payload shape on first use.
              </p>
            </div>

            {form.provider === "auto" && (
              <div>
                <Label htmlFor="ingest-sample">Sample payload (optional JSON)</Label>
                <textarea
                  id="ingest-sample"
                  value={sampleText}
                  onChange={(e) => {
                    setSampleText(e.target.value);
                    setSampleError("");
                  }}
                  rows={6}
                  placeholder={'{\n  "alerts": [{"labels": {"alertname": "..."}}]\n}'}
                  className="mt-1 block w-full rounded-md border border-border-subtle bg-bg-panel px-3 py-2 font-mono text-xs text-fg-primary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
                />
                <p className="mt-1 text-xs text-fg-muted">
                  Paste a sample alert JSON to pre-train the token. Skips the
                  LLM call on the first real webhook of this shape.
                </p>
                {sampleError && (
                  <p className="mt-1 text-xs text-status-critical">{sampleError}</p>
                )}
              </div>
            )}

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

// ---------------------------------------------------------------------------
// Agent Team Profiles (multi-agent support — Phase 3)
// ---------------------------------------------------------------------------

const AGENT_ROLE_OPTIONS: Array<{
  value: AgentRole;
  label: string;
  description: string;
}> = [
  {
    value: "incident_commander",
    label: "Incident Commander",
    description: "Keeps the output decision-oriented and impact-aware.",
  },
  {
    value: "investigator",
    label: "Investigator",
    description: "Focuses on evidence, failure domains, and root-cause signals.",
  },
  {
    value: "skeptic",
    label: "Skeptic",
    description: "Challenges assumptions and surfaces uncertainty or missing data.",
  },
  {
    value: "remediator",
    label: "Remediator",
    description: "Pushes toward safe action ordering and rollback-aware plans.",
  },
];

type AgentTeamProfileFormState = {
  name: string;
  description: string;
  roles: AgentRole[];
  is_active: boolean;
  is_default: boolean;
};

function createAgentTeamProfileFormState(
  current: AgentTeamProfileResponse | null,
): AgentTeamProfileFormState {
  return {
    name: current?.name ?? "",
    description: current?.description ?? "",
    roles: current?.roles ?? ["incident_commander", "investigator", "skeptic"],
    is_active: current?.is_active ?? true,
    is_default: current?.is_default ?? false,
  };
}

function AgentTeamProfileModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  initialProfile,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: AgentTeamProfileFormState) => Promise<void>;
  saving: boolean;
  error: string;
  initialProfile: AgentTeamProfileResponse | null;
}) {
  const [form, setForm] = useState<AgentTeamProfileFormState>(() =>
    createAgentTeamProfileFormState(initialProfile),
  );

  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setForm(createAgentTeamProfileFormState(initialProfile));
  }, [open, initialProfile]);

  function setField<K extends keyof AgentTeamProfileFormState>(
    key: K,
    value: AgentTeamProfileFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function toggleRole(role: AgentRole) {
    setForm((current) => {
      const exists = current.roles.includes(role);
      return {
        ...current,
        roles: exists
          ? current.roles.filter((item) => item !== role)
          : [...current.roles, role],
      };
    });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(form);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialProfile ? "Edit Agent Team" : "Add Agent Team"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="agent-team-name">Profile Name</Label>
            <Input
              id="agent-team-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="triage-council"
              required
            />
          </div>
          <div>
            <Label htmlFor="agent-team-desc">Description</Label>
            <Input
              id="agent-team-desc"
              value={form.description}
              onChange={(e) => setField("description", e.target.value)}
              placeholder="Balanced triage with challenge + remediation review"
            />
          </div>
        </div>

        <div>
          <Label>Specialist Roles</Label>
          <p className="mt-1 text-xs text-fg-muted">
            Selected roles each produce their own reasoning pass for observe,
            diagnose, plan, verify, and summarize. OpsMender then synthesizes them
            into one final answer while keeping `tier_gate` and `execute`
            single-path and deterministic.
          </p>
          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
            {AGENT_ROLE_OPTIONS.map((role) => {
              const checked = form.roles.includes(role.value);
              return (
                <label
                  key={role.value}
                  className={`rounded-lg border px-4 py-3 text-sm ${
                    checked
                      ? "border-accent bg-accent-bg"
                      : "border-border-subtle bg-bg-elevated"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleRole(role.value)}
                      className="mt-0.5 h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
                    />
                    <div>
                      <p className="font-medium text-fg-primary">{role.label}</p>
                      <p className="mt-1 text-xs text-fg-secondary">{role.description}</p>
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-primary">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setField("is_active", e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
            />
            Active (available when starting sessions)
          </label>
          <label className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-primary">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => setField("is_default", e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
            />
            Default agent team
          </label>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={saving}
            disabled={!form.name.trim() || form.roles.length === 0}
          >
            <Save size={13} />{" "}
            {initialProfile ? "Save Changes" : "Create Team"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function AgentTeamProfileSection({
  profiles,
  onReload,
  canEdit,
}: {
  profiles: AgentTeamProfileResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AgentTeamProfileResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(profile: AgentTeamProfileResponse) {
    setEditing(profile);
    setError("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: AgentTeamProfileFormState) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: AgentTeamProfileUpsert = {
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        roles: form.roles,
        is_active: form.is_active,
        is_default: form.is_default,
      };
      if (editing) {
        await updateAgentTeamProfile(editing.id, payload);
        setNotice("Agent team updated.");
      } else {
        await createAgentTeamProfile(payload);
        setNotice("Agent team created.");
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

  async function handleDelete(profile: AgentTeamProfileResponse) {
    const confirmed = window.confirm(
      `Delete agent team "${profile.name}"?`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteAgentTeamProfile(profile.id);
      setNotice("Agent team deleted.");
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <Section
      title="Agent Teams"
      description="Saved agent teams run multiple specialist reasoning passes inside the same OpsMender workflow, while execution still flows through the normal tier gate and execute path."
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-fg-secondary">
            {profiles.length} saved team{profiles.length === 1 ? "" : "s"}
          </p>
          {!canEdit && (
            <p className="text-sm text-fg-secondary">
              Admin role required to manage agent teams.
            </p>
          )}
        </div>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> Add Agent Team
        </Button>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      {profiles.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
          No agent teams yet. Sessions will use OpsMender&apos;s default single-agent reasoning.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border-subtle">
          <table className="min-w-full divide-y divide-border-subtle text-sm">
            <thead className="bg-bg-elevated text-left text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              <tr>
                <th className="px-4 py-3">Profile</th>
                <th className="px-4 py-3">Roles</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle bg-bg-panel">
              {profiles.map((profile) => (
                <tr key={profile.id}>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-fg-primary">{profile.name}</span>
                      {profile.is_default && <Badge>Default</Badge>}
                    </div>
                    {profile.description && (
                      <p className="mt-1 text-xs text-fg-secondary">{profile.description}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex flex-wrap gap-1.5">
                      {profile.roles.map((role) => {
                        const option = AGENT_ROLE_OPTIONS.find((item) => item.value === role);
                        return <Badge key={role}>{option?.label ?? role}</Badge>;
                      })}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Badge variant={profile.is_active ? "resolved" : "closed"}>
                      {profile.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openEditModal(profile)}
                        disabled={!canEdit}
                      >
                        <Pencil size={13} /> Edit
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDelete(profile)}
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

      {modalOpen && (
        <AgentTeamProfileModal
          open={modalOpen}
          onClose={closeModal}
          onSubmit={handleSubmit}
          saving={saving}
          error={error}
          initialProfile={editing}
        />
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Workflow Profiles (custom workflow builder — Phase 3)
// ---------------------------------------------------------------------------

const WORKFLOW_NODE_OPTIONS: Array<{ value: WorkflowNode; label: string }> = [
  { value: "observe", label: "Observe" },
  { value: "diagnose", label: "Diagnose" },
  { value: "plan", label: "Plan" },
  { value: "tier_gate", label: "Tier Gate" },
  { value: "execute", label: "Execute" },
  { value: "verify", label: "Verify" },
  { value: "summarize", label: "Summarize" },
];

type WorkflowProfileFormState = {
  name: string;
  description: string;
  node_order: WorkflowNode[];
  is_active: boolean;
  is_default: boolean;
};

function createWorkflowProfileFormState(
  current: WorkflowProfileResponse | null,
): WorkflowProfileFormState {
  return {
    name: current?.name ?? "",
    description: current?.description ?? "",
    node_order: current?.node_order ?? [
      "observe",
      "diagnose",
      "plan",
      "tier_gate",
      "execute",
      "verify",
      "summarize",
    ],
    is_active: current?.is_active ?? true,
    is_default: current?.is_default ?? false,
  };
}

function WorkflowProfileModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  initialProfile,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: WorkflowProfileFormState) => Promise<void>;
  saving: boolean;
  error: string;
  initialProfile: WorkflowProfileResponse | null;
}) {
  const [form, setForm] = useState<WorkflowProfileFormState>(() =>
    createWorkflowProfileFormState(initialProfile),
  );

  function setField<K extends keyof WorkflowProfileFormState>(
    key: K,
    value: WorkflowProfileFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function moveNode(index: number, direction: -1 | 1) {
    setForm((current) => {
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= current.node_order.length) {
        return current;
      }
      const updated = [...current.node_order];
      const [node] = updated.splice(index, 1);
      updated.splice(nextIndex, 0, node);
      return { ...current, node_order: updated };
    });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(form);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialProfile ? "Edit Workflow Profile" : "Add Workflow Profile"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="workflow-name">Profile Name</Label>
            <Input
              id="workflow-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="default-linear"
              required
            />
          </div>
          <div>
            <Label htmlFor="workflow-desc">Description</Label>
            <Input
              id="workflow-desc"
              value={form.description}
              onChange={(e) => setField("description", e.target.value)}
              placeholder="Standard observe → diagnose → plan flow"
            />
          </div>
        </div>

        <div>
          <Label>Node Order</Label>
          <p className="mt-1 text-xs text-fg-muted">
            Reorder the fixed OpsMender nodes. Safety rules are enforced server-side:
            `execute` requires `tier_gate` immediately before it.
          </p>
          <div className="mt-3 space-y-2">
            {form.node_order.map((node, index) => {
              const option = WORKFLOW_NODE_OPTIONS.find((item) => item.value === node);
              return (
                <div
                  key={`${node}-${index}`}
                  className="flex items-center justify-between rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2"
                >
                  <div className="flex items-center gap-3">
                    <Badge>{index + 1}</Badge>
                    <span className="text-sm font-medium text-fg-primary">
                      {option?.label ?? node}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => moveNode(index, -1)}
                      disabled={index === 0}
                    >
                      Up
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => moveNode(index, 1)}
                      disabled={index === form.node_order.length - 1}
                    >
                      Down
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-primary">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setField("is_active", e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
            />
            Active (available when starting sessions)
          </label>
          <label className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-primary">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => setField("is_default", e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
            />
            Default workflow profile
          </label>
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={saving}
            disabled={!form.name.trim() || form.node_order.length === 0}
          >
            <Save size={13} />{" "}
            {initialProfile ? "Save Changes" : "Create Profile"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function WorkflowProfileSection({
  profiles,
  onReload,
  canEdit,
}: {
  profiles: WorkflowProfileResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<WorkflowProfileResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(profile: WorkflowProfileResponse) {
    setEditing(profile);
    setError("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: WorkflowProfileFormState) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: WorkflowProfileUpsert = {
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        node_order: form.node_order,
        is_active: form.is_active,
        is_default: form.is_default,
      };
      if (editing) {
        await updateWorkflowProfile(editing.id, payload);
        setNotice("Workflow profile updated.");
      } else {
        await createWorkflowProfile(payload);
        setNotice("Workflow profile created.");
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

  async function handleDelete(profile: WorkflowProfileResponse) {
    const confirmed = window.confirm(
      `Delete workflow profile "${profile.name}"?`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteWorkflowProfile(profile.id);
      setNotice("Workflow profile deleted.");
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <Section
      title="Workflow Profiles"
      description="Saved workflow profiles let operators choose which built-in OpsMender nodes run, and in what order, while preserving the tier-gate safety rules."
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-fg-secondary">
            {profiles.length} saved profile{profiles.length === 1 ? "" : "s"}
          </p>
          {!canEdit && (
            <p className="text-sm text-fg-secondary">
              Admin role required to manage workflow profiles.
            </p>
          )}
        </div>
        <Button onClick={openCreateModal} disabled={!canEdit}>
          <Plus size={14} /> Add Workflow Profile
        </Button>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      {profiles.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
          No workflow profiles yet. Sessions will use OpsMender&apos;s built-in default flow.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border-subtle">
          <table className="min-w-full divide-y divide-border-subtle text-sm">
            <thead className="bg-bg-elevated text-left text-xs font-semibold uppercase tracking-wide text-fg-secondary">
              <tr>
                <th className="px-4 py-3">Profile</th>
                <th className="px-4 py-3">Nodes</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle bg-bg-panel">
              {profiles.map((profile) => (
                <tr key={profile.id}>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-fg-primary">{profile.name}</span>
                      {profile.is_default && <Badge>Default</Badge>}
                    </div>
                    {profile.description && (
                      <p className="mt-1 text-xs text-fg-secondary">{profile.description}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex flex-wrap gap-1.5">
                      {profile.node_order.map((node) => (
                        <Badge key={node}>{node}</Badge>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Badge variant={profile.is_active ? "resolved" : "closed"}>
                      {profile.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openEditModal(profile)}
                        disabled={!canEdit}
                      >
                        <Pencil size={13} /> Edit
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDelete(profile)}
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

      {modalOpen && (
        <WorkflowProfileModal
          open={modalOpen}
          onClose={closeModal}
          onSubmit={handleSubmit}
          saving={saving}
          error={error}
          initialProfile={editing}
        />
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Outbound Webhook Triggers
// ---------------------------------------------------------------------------

const WEBHOOK_EVENT_OPTIONS: Array<{
  value: WebhookTriggerEventType;
  label: string;
}> = [
  { value: "*", label: "All session events" },
  { value: "session.created", label: "Session created" },
  { value: "session.awaiting_approval", label: "Awaiting approval" },
  { value: "session.active", label: "Session active" },
  { value: "session.completed", label: "Session completed" },
  { value: "session.failed", label: "Session failed" },
  { value: "session.timed_out", label: "Session timed out" },
];

type WebhookFieldMode = "replace" | "preserve" | "clear";

type WebhookFormState = {
  name: string;
  url: string;
  format: WebhookTriggerFormat;
  event_types: WebhookTriggerEventType[];
  headersText: string;
  headersMode: WebhookFieldMode;
  token: string;
  tokenMode: WebhookFieldMode;
  is_active: boolean;
};

type WebhookTestState = {
  status: "idle" | "running" | "success" | "failure";
  result?: WebhookTriggerTestResponse;
};

function createWebhookFormState(
  current: WebhookTriggerResponse | null,
): WebhookFormState {
  return {
    name: current?.name ?? "",
    url: current?.url ?? "",
    format: current?.format ?? "generic",
    event_types: current?.event_types?.length
      ? current.event_types
      : ["session.completed"],
    headersText: "",
    headersMode: current?.header_names.length ? "preserve" : "replace",
    token: "",
    tokenMode: current?.has_token ? "preserve" : "replace",
    is_active: current?.is_active ?? true,
  };
}

function parseWebhookHeaders(
  value: string,
): { headers: Record<string, string> | null; error: string | null } {
  const trimmed = value.trim();
  if (!trimmed) {
    return { headers: null, error: null };
  }

  const headers: Record<string, string> = {};
  for (const rawLine of trimmed.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const separator = line.indexOf(":");
    if (separator <= 0) {
      return {
        headers: null,
        error: `Invalid header line "${line}". Use "Header-Name: value".`,
      };
    }
    const key = line.slice(0, separator).trim();
    const headerValue = line.slice(separator + 1).trim();
    if (!key) {
      return {
        headers: null,
        error: `Invalid header line "${line}". Header name is required.`,
      };
    }
    headers[key] = headerValue;
  }

  return { headers, error: null };
}

function buildWebhookPayload(
  form: WebhookFormState,
  initialTrigger: WebhookTriggerResponse | null,
): { payload: WebhookTriggerUpsert | null; error: string | null } {
  if (form.event_types.length === 0) {
    return { payload: null, error: "Select at least one session event." };
  }

  const payload: WebhookTriggerUpsert = {
    name: form.name.trim(),
    url: form.url.trim(),
    format: form.format,
    event_types: form.event_types,
    is_active: form.is_active,
  };

  if (!payload.name || !payload.url) {
    return { payload: null, error: "Name and URL are required." };
  }

  if (form.headersMode === "clear") {
    payload.clear_headers = true;
  } else if (form.headersMode === "replace" || !initialTrigger) {
    const { headers, error } = parseWebhookHeaders(form.headersText);
    if (error) return { payload: null, error };
    if (headers && Object.keys(headers).length > 0) {
      payload.headers = headers;
    } else if (initialTrigger) {
      payload.clear_headers = true;
    }
  }

  if (form.tokenMode === "clear") {
    payload.clear_token = true;
  } else if (form.tokenMode === "replace" || !initialTrigger) {
    const token = form.token.trim();
    if (token) {
      payload.token = token;
    } else if (initialTrigger) {
      payload.clear_token = true;
    }
  }

  return { payload, error: null };
}

function formatRelativeTimestamp(timestamp: string | null): string {
  if (!timestamp) return "Never";
  const value = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - value.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays}d ago`;
  return value.toLocaleDateString();
}

function WebhookTestPill({ state }: { state: WebhookTestState }) {
  if (state.status === "idle") return null;
  if (state.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-elevated px-2 py-0.5 text-xs text-fg-secondary">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bg-elevated" />
        Testing…
      </span>
    );
  }
  if (state.status === "success") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-status-low-border bg-status-low-bg px-2 py-0.5 text-xs text-status-low">
        <CheckCircle2 size={12} /> Delivered
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-status-critical-border bg-status-critical-bg px-2 py-0.5 text-xs text-status-critical"
      title={state.result?.detail}
    >
      <XCircle size={12} /> Failed
    </span>
  );
}

function WebhookTriggerModal({
  open,
  onClose,
  onSubmit,
  saving,
  error,
  initialTrigger,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (form: WebhookFormState) => Promise<void>;
  saving: boolean;
  error: string;
  initialTrigger: WebhookTriggerResponse | null;
}) {
  const [form, setForm] = useState<WebhookFormState>(() =>
    createWebhookFormState(initialTrigger),
  );

  function setField<K extends keyof WebhookFormState>(
    key: K,
    value: WebhookFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function toggleEvent(eventType: WebhookTriggerEventType) {
    setForm((current) => {
      const selected = new Set(current.event_types);
      if (eventType === "*") {
        return {
          ...current,
          event_types: selected.has("*") ? [] : ["*"],
        };
      }

      selected.delete("*");
      if (selected.has(eventType)) {
        selected.delete(eventType);
      } else {
        selected.add(eventType);
      }
      return {
        ...current,
        event_types: Array.from(selected) as WebhookTriggerEventType[],
      };
    });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(form);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initialTrigger ? "Edit Webhook Trigger" : "Add Webhook Trigger"}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="webhook-name">Trigger Name</Label>
            <Input
              id="webhook-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="ops-oncall"
              required
            />
          </div>
          <div>
            <Label htmlFor="webhook-url">Destination URL</Label>
            <Input
              id="webhook-url"
              type="url"
              value={form.url}
              onChange={(e) => setField("url", e.target.value)}
              placeholder={
                form.format === "slack"
                  ? "https://hooks.slack.com/services/..."
                  : form.format === "teams"
                    ? "https://prod-...logic.azure.com/... or Teams workflow URL"
                    : form.format === "sumo"
                      ? "https://endpoint.collection.us2.sumologic.com/receiver/v1/http/..."
                    : "https://hooks.example/opsmender"
              }
              required
            />
          </div>
        </div>

        <div>
          <Label htmlFor="webhook-format">Delivery Format</Label>
          <Select
            id="webhook-format"
            value={form.format}
            onChange={(e) =>
              setField("format", e.target.value as WebhookTriggerFormat)
            }
          >
            <option value="generic">Generic JSON</option>
            <option value="slack">Slack incoming webhook</option>
            <option value="teams">Teams webhook workflow</option>
            <option value="sumo">Sumo Logic JSON event</option>
          </Select>
          <p className="mt-1 text-xs text-fg-muted">
            {form.format === "slack" &&
              "Sends Slack-compatible text plus Block Kit sections to an incoming webhook URL."}
            {form.format === "teams" &&
              "Sends a Teams workflow-friendly text payload. Use a Teams Workflows webhook URL."}
            {form.format === "sumo" &&
              "Sends a log-friendly JSON event optimized for Sumo HTTP sources or webhook-style JSON ingestion endpoints."}
            {form.format === "generic" &&
              "Sends OpsMender's full normalized session-event JSON payload."}
          </p>
        </div>

        <div>
          <Label>Session Events</Label>
          <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
            {WEBHOOK_EVENT_OPTIONS.map((option) => {
              const checked = form.event_types.includes(option.value);
              const disabled =
                option.value !== "*" && form.event_types.includes("*");
              return (
                <label
                  key={option.value}
                  className={`flex items-start gap-3 rounded-lg border px-3 py-2 text-sm ${
                    checked
                      ? "border-accent bg-accent-bg text-accent"
                      : "border-border-subtle bg-bg-panel text-fg-primary"
                  } ${disabled ? "opacity-50" : ""}`}
                >
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => toggleEvent(option.value)}
                  />
                  <span>{option.label}</span>
                </label>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="webhook-headers-mode">Headers</Label>
            <Select
              id="webhook-headers-mode"
              value={form.headersMode}
              onChange={(e) =>
                setField("headersMode", e.target.value as WebhookFieldMode)
              }
            >
              {initialTrigger && (
                <option value="preserve">Preserve stored headers</option>
              )}
              <option value="replace">
                {initialTrigger ? "Replace headers" : "Set headers now"}
              </option>
              {initialTrigger && <option value="clear">Clear headers</option>}
            </Select>
            {initialTrigger?.header_names.length ? (
              <p className="mt-1 text-xs text-fg-muted">
                Stored headers: {initialTrigger.header_names.join(", ")}
              </p>
            ) : (
              <p className="mt-1 text-xs text-fg-muted">
                Optional static headers sent with every delivery. Usually not needed for Slack, Teams, or Sumo collector URLs.
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="webhook-token-mode">Bearer Token</Label>
            <Select
              id="webhook-token-mode"
              value={form.tokenMode}
              onChange={(e) =>
                setField("tokenMode", e.target.value as WebhookFieldMode)
              }
            >
              {initialTrigger && (
                <option value="preserve">Preserve stored token</option>
              )}
              <option value="replace">
                {initialTrigger ? "Replace token" : "Set token now"}
              </option>
              {initialTrigger && <option value="clear">Clear token</option>}
            </Select>
            <p className="mt-1 text-xs text-fg-muted">
              {initialTrigger?.has_token
                ? "A bearer token is already stored for this trigger."
                : "Optional Authorization: Bearer token. Usually not needed for Slack, Teams, or Sumo collector URLs."}
            </p>
          </div>
        </div>

        {form.headersMode === "replace" && (
          <div>
            <Label htmlFor="webhook-headers">Header Overrides</Label>
            <Textarea
              id="webhook-headers"
              value={form.headersText}
              onChange={(e) => setField("headersText", e.target.value)}
              rows={5}
              placeholder={"X-Team: platform\nX-Environment: production"}
            />
            <p className="mt-1 text-xs text-fg-muted">
              One header per line in the form <code>Header-Name: value</code>.
            </p>
          </div>
        )}

        {form.tokenMode === "replace" && (
          <div>
            <Label htmlFor="webhook-token">Bearer Token</Label>
            <Input
              id="webhook-token"
              value={form.token}
              onChange={(e) => setField("token", e.target.value)}
              placeholder="secret-token"
            />
          </div>
        )}

        <label className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-primary">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setField("is_active", e.target.checked)}
            className="h-4 w-4 rounded border-border-strong text-accent focus:ring-accent"
          />
          Active (deliver subscribed events)
        </label>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            loading={saving}
            disabled={!form.name.trim() || !form.url.trim()}
          >
            <Save size={13} />{" "}
            {initialTrigger ? "Save Changes" : "Create Trigger"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function WebhookTriggerSection({
  triggers,
  onReload,
  canEdit,
}: {
  triggers: WebhookTriggerResponse[];
  onReload: () => Promise<void>;
  canEdit: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<WebhookTriggerResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testStates, setTestStates] = useState<Record<string, WebhookTestState>>(
    {},
  );

  function openCreateModal() {
    setEditing(null);
    setError("");
    setModalOpen(true);
  }

  function openEditModal(trigger: WebhookTriggerResponse) {
    setEditing(trigger);
    setError("");
    setModalOpen(true);
  }

  function closeModal() {
    if (saving) return;
    setModalOpen(false);
    setEditing(null);
    setError("");
  }

  async function handleSubmit(form: WebhookFormState) {
    const { payload, error: buildError } = buildWebhookPayload(form, editing);
    if (buildError || !payload) {
      setError(buildError ?? "Invalid form values.");
      return;
    }

    setSaving(true);
    setError("");
    setNotice("");
    try {
      if (editing) {
        await updateWebhookTrigger(editing.id, payload);
        setNotice("Webhook trigger updated.");
      } else {
        await createWebhookTrigger(payload);
        setNotice("Webhook trigger created.");
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

  async function handleDelete(trigger: WebhookTriggerResponse) {
    const confirmed = window.confirm(
      `Delete webhook trigger "${trigger.name}"?`,
    );
    if (!confirmed) return;

    setError("");
    setNotice("");
    try {
      await deleteWebhookTrigger(trigger.id);
      setNotice("Webhook trigger deleted.");
      setTestStates((current) => {
        const next = { ...current };
        delete next[trigger.id];
        return next;
      });
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleTest(trigger: WebhookTriggerResponse) {
    setTestStates((current) => ({
      ...current,
      [trigger.id]: { status: "running" },
    }));

    try {
      const result = await testWebhookTrigger(trigger.id);
      setTestStates((current) => ({
        ...current,
        [trigger.id]: {
          status: result.success ? "success" : "failure",
          result,
        },
      }));
    } catch (err) {
      setTestStates((current) => ({
        ...current,
        [trigger.id]: {
          status: "failure",
          result: {
            success: false,
            detail: err instanceof Error ? err.message : "Request failed",
            status_code: null,
            event_type: "webhook.test",
          },
        },
      }));
    }
  }

  const formatOptions = useMemo(
    () =>
      Array.from(
        new Map(
          triggers.map((t) => [t.format, { value: t.format, label: t.format }]),
        ).values(),
      ),
    [triggers],
  );

  const webhookColumns = useMemo<DataTableColumn<WebhookTriggerResponse>[]>(
    () => [
      {
        id: "name",
        label: "Trigger",
        accessor: (trigger) => trigger.name,
        sortable: true,
        searchable: true,
        cell: (trigger) => (
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Bell size={14} className="text-fg-muted" />
              <span className="font-medium text-fg-primary">{trigger.name}</span>
            </div>
            <p className="mt-1 break-all font-mono text-xs text-fg-secondary">
              {trigger.url}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {trigger.has_token && (
                <span className="text-xs text-fg-secondary">
                  Bearer token stored
                </span>
              )}
              {trigger.header_names.length > 0 && (
                <span className="text-xs text-fg-secondary">
                  Headers: {trigger.header_names.join(", ")}
                </span>
              )}
            </div>
          </div>
        ),
      },
      {
        id: "format",
        label: "Format",
        accessor: (trigger) => trigger.format,
        sortable: true,
        filterChips: {
          options: formatOptions,
          valueOf: (trigger) => trigger.format,
        },
        cell: (trigger) => <Badge>{trigger.format}</Badge>,
      },
      {
        id: "events",
        label: "Events",
        accessor: (trigger) => trigger.event_types.join(","),
        cell: (trigger) => (
          <div className="flex flex-wrap gap-1.5">
            {trigger.event_types.map((eventType) => (
              <Badge key={eventType}>
                {eventType === "*" ? "all" : eventType.replace("session.", "")}
              </Badge>
            ))}
          </div>
        ),
      },
      {
        id: "delivery",
        label: "Delivery",
        accessor: (trigger) => trigger.last_triggered_at ?? "",
        sortable: true,
        cell: (trigger) => (
          <div>
            <p className="text-sm text-fg-primary">
              Last send: {formatRelativeTimestamp(trigger.last_triggered_at)}
            </p>
            {trigger.last_error ? (
              <p
                className="mt-1 line-clamp-2 text-xs text-status-critical"
                title={trigger.last_error}
              >
                {trigger.last_error}
              </p>
            ) : (
              <p className="mt-1 text-xs text-fg-muted">
                No delivery errors recorded.
              </p>
            )}
          </div>
        ),
      },
      {
        id: "status",
        label: "Status",
        accessor: (trigger) => (trigger.is_active ? "active" : "inactive"),
        sortable: true,
        filterChips: {
          options: [
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
          ],
          valueOf: (trigger) => (trigger.is_active ? "active" : "inactive"),
        },
        cell: (trigger) => {
          const testState = testStates[trigger.id] ?? { status: "idle" };
          return (
            <div className="flex flex-col gap-1.5">
              <Badge variant={trigger.is_active ? "resolved" : "closed"}>
                {trigger.is_active ? "Active" : "Inactive"}
              </Badge>
              <WebhookTestPill state={testState} />
            </div>
          );
        },
      },
    ],
    [formatOptions, testStates],
  );

  return (
    <Section
      title="Outbound Webhooks"
      description="Saved triggers notify external systems when session state changes. This uses the existing generic webhook backend."
    >
      {!canEdit && (
        <p className="text-sm text-fg-secondary">
          Admin role required to manage outbound webhook triggers.
        </p>
      )}

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      <DataTable
        rows={triggers}
        columns={webhookColumns}
        rowKey={(trigger) => trigger.id}
        storageKey="opsmender:webhook-triggers-table"
        searchPlaceholder="Search by trigger name or URL…"
        toolbarRight={
          <>
            <span className="text-sm text-fg-secondary">
              {triggers.length} saved trigger{triggers.length === 1 ? "" : "s"}
            </span>
            <Button onClick={openCreateModal} disabled={!canEdit}>
              <Plus size={14} /> Add Trigger
            </Button>
          </>
        }
        empty={
          <div className="rounded-lg border border-dashed border-border-subtle bg-bg-elevated px-4 py-6 text-sm text-fg-secondary">
            No outbound webhook triggers yet. Add one to deliver OpsMender session events to downstream systems.
          </div>
        }
        rowActions={(trigger) => {
          const testState = testStates[trigger.id] ?? { status: "idle" };
          return (
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => handleTest(trigger)}
                loading={testState.status === "running"}
                disabled={!canEdit}
              >
                <Send size={13} /> Test
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => openEditModal(trigger)}
                disabled={!canEdit}
              >
                <Pencil size={13} /> Edit
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => handleDelete(trigger)}
                disabled={!canEdit}
              >
                <Trash2 size={13} /> Delete
              </Button>
            </div>
          );
        }}
      />

      {modalOpen && (
        <WebhookTriggerModal
          open={modalOpen}
          onClose={closeModal}
          onSubmit={handleSubmit}
          saving={saving}
          error={error}
          initialTrigger={editing}
        />
      )}
    </Section>
  );
}

const RETENTION_CATEGORY_LABELS: Record<string, string> = {
  audit_entries: "Audit entries",
  ingest_log: "Inbound webhook payloads",
  incident_memory_recall_log: "Memory recall log",
  bot_action_audit: "Bot action audit",
  incident_memories: "Incident memories",
};

const RETENTION_CATEGORY_DESCRIPTIONS: Record<string, string> = {
  audit_entries:
    "Every agent tool call, every block, every node transition. High-volume on busy clusters; default 90-day TTL.",
  ingest_log:
    "Raw inbound webhook payloads kept for replay/debugging. Highest per-row cost (~5 KB avg); usually safe to prune aggressively.",
  incident_memory_recall_log:
    "One row per (memory, session) surfacing event. Tiny rows but volume grows with traffic.",
  bot_action_audit:
    "Inbound bot commands (Slack/Teams ack/take/resolve). Default 90-day TTL.",
  incident_memories:
    "Operator- and agent-curated lessons. Never auto-deleted — manage manually from /dashboard/memories.",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function RetentionSection({ canEdit }: { canEdit: boolean }) {
  const [status, setStatus] = useState<RetentionStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<RetentionRunReportResponse | null>(
    null,
  );
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  // Per-category draft state (string so the input can be cleared mid-typing).
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await getRetentionStatus();
      setStatus(res);
      setDrafts(
        res.configs.reduce<Record<string, string>>((acc, cfg) => {
          acc[cfg.category] =
            cfg.ttl_days === null ? "" : String(cfg.ttl_days);
          return acc;
        }, {}),
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load retention status",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const storageByCategory = useMemo(() => {
    const map = new Map<string, RetentionCategoryStorage>();
    for (const row of status?.storage ?? []) map.set(row.category, row);
    return map;
  }, [status]);

  async function saveCategory(
    category: string,
    ttl_days: number | null,
  ) {
    if (!canEdit) return;
    setSaving(category);
    setError("");
    setNotice("");
    try {
      const res = await updateRetention({
        configs: [{ category, ttl_days }],
      });
      setStatus(res);
      setDrafts(
        res.configs.reduce<Record<string, string>>((acc, cfg) => {
          acc[cfg.category] =
            cfg.ttl_days === null ? "" : String(cfg.ttl_days);
          return acc;
        }, {}),
      );
      setNotice(
        ttl_days === null
          ? `${RETENTION_CATEGORY_LABELS[category] ?? category}: auto-deletion disabled.`
          : `${RETENTION_CATEGORY_LABELS[category] ?? category}: TTL set to ${ttl_days} day${ttl_days === 1 ? "" : "s"}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(null);
    }
  }

  async function handleRunNow() {
    if (!canEdit) return;
    setRunning(true);
    setError("");
    setNotice("");
    try {
      const report = await runRetentionNow();
      setLastRun(report);
      setNotice(
        `Pruner ran: deleted ${report.total_deleted} row${report.total_deleted === 1 ? "" : "s"}` +
          (report.total_errors > 0
            ? `, ${report.total_errors} error${report.total_errors === 1 ? "" : "s"}`
            : ""),
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pruner run failed");
    } finally {
      setRunning(false);
    }
  }

  if (loading) {
    return (
      <Section
        title="Storage & retention"
        description="Per-category log retention windows + storage estimate."
      >
        <p className="text-sm text-fg-muted">Loading retention status…</p>
      </Section>
    );
  }

  if (!status) {
    return (
      <Section
        title="Storage & retention"
        description="Per-category log retention windows + storage estimate."
      >
        <FormError message={error || "Retention status unavailable."} />
      </Section>
    );
  }

  const totalEstimatedBytes = status.storage
    .filter((row) => !row.non_prunable)
    .reduce((acc, row) => acc + row.estimated_bytes, 0);
  const memoryStorage = storageByCategory.get("incident_memories");

  return (
    <Section
      title="Storage & retention"
      description={`Logs auto-prune after ${status.default_ttl_days} days by default. Shorten, extend, or disable per category to bound storage growth and avoid OOM / out-of-disk errors. Memories are operator-curated and never auto-deleted.`}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Estimated log storage
          </p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-fg-primary">
            {formatBytes(totalEstimatedBytes)}
          </p>
          <p className="mt-1 text-xs text-fg-muted">
            Sum of the four prunable categories.
          </p>
        </div>
        <div className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Memories (non-prunable)
          </p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-fg-primary">
            {memoryStorage
              ? `${memoryStorage.row_count} row${memoryStorage.row_count === 1 ? "" : "s"}`
              : "—"}
          </p>
          <p className="mt-1 text-xs text-fg-muted">
            {memoryStorage
              ? `~${formatBytes(memoryStorage.estimated_bytes)} — manage at /dashboard/memories`
              : "Manage at /dashboard/memories"}
          </p>
        </div>
        <div className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm">
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Pruner
          </p>
          <p
            className={`mt-2 text-2xl font-semibold tracking-tight ${status.scheduler_enabled ? "text-status-low" : "text-status-medium"}`}
          >
            {status.scheduler_enabled ? "Enabled" : "Disabled"}
          </p>
          <p className="mt-1 text-xs text-fg-muted">
            {status.scheduler_enabled
              ? "Runs every 6 hours. Override per category below."
              : "Disabled at process level via OPSMENDER_RETENTION_ENABLED=false."}
          </p>
        </div>
      </div>

      {error && <FormError message={error} />}
      {notice && <p className="text-sm text-status-low">{notice}</p>}

      <div className="overflow-x-auto rounded-xl border border-border-subtle">
        <table className="min-w-full divide-y divide-border-subtle text-sm">
          <thead className="bg-bg-elevated text-left text-xs font-semibold uppercase tracking-wide text-fg-secondary">
            <tr>
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3 text-right">Rows</th>
              <th className="px-4 py-3 text-right">~Size</th>
              <th className="px-4 py-3">Retention</th>
              <th className="px-4 py-3">Last pruned</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle bg-bg-panel">
            {status.configs.map((cfg) => {
              const storage = storageByCategory.get(cfg.category);
              const draft = drafts[cfg.category] ?? "";
              const draftNum = draft.trim() === "" ? null : Number(draft);
              const draftInvalid =
                draft.trim() !== "" &&
                (Number.isNaN(draftNum as number) ||
                  (draftNum !== null && draftNum < 1));
              const dirty =
                (draftNum ?? null) !==
                (cfg.ttl_days === null ? null : cfg.ttl_days);
              return (
                <tr key={cfg.category} className="align-top">
                  <td className="px-4 py-3">
                    <div className="font-medium text-fg-primary">
                      {RETENTION_CATEGORY_LABELS[cfg.category] ?? cfg.category}
                    </div>
                    <p className="mt-1 max-w-md text-xs text-fg-muted">
                      {RETENTION_CATEGORY_DESCRIPTIONS[cfg.category] ?? ""}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-right text-fg-primary tabular-nums">
                    {storage ? storage.row_count.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-3 text-right text-fg-secondary tabular-nums">
                    {storage ? formatBytes(storage.estimated_bytes) : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {cfg.ttl_days === null ? (
                      <Badge variant="medium">Auto-delete disabled</Badge>
                    ) : (
                      <Badge variant={cfg.is_default ? "default" : "info"}>
                        {cfg.ttl_days} day{cfg.ttl_days === 1 ? "" : "s"}
                        {cfg.is_default ? " (default)" : ""}
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-muted">
                    {cfg.last_pruned_at
                      ? `${new Date(cfg.last_pruned_at).toLocaleString()} · ${cfg.last_pruned_count ?? 0} row${
                          (cfg.last_pruned_count ?? 0) === 1 ? "" : "s"
                        }`
                      : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <Input
                        type="number"
                        min={1}
                        value={draft}
                        onChange={(e) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [cfg.category]: e.target.value,
                          }))
                        }
                        disabled={!canEdit || saving === cfg.category}
                        className="w-20"
                        placeholder="Days"
                      />
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() =>
                          saveCategory(cfg.category, draftNum)
                        }
                        disabled={
                          !canEdit ||
                          !dirty ||
                          draftInvalid ||
                          saving === cfg.category
                        }
                        loading={saving === cfg.category}
                      >
                        Save
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => saveCategory(cfg.category, null)}
                        disabled={
                          !canEdit ||
                          cfg.ttl_days === null ||
                          saving === cfg.category
                        }
                        title="Disable auto-deletion for this category"
                      >
                        Disable
                      </Button>
                    </div>
                    {draftInvalid && (
                      <p className="mt-1 text-right text-xs text-status-critical">
                        Must be ≥ 1 day.
                      </p>
                    )}
                  </td>
                </tr>
              );
            })}
            {memoryStorage && (
              <tr className="align-top bg-bg-elevated/40">
                <td className="px-4 py-3">
                  <div className="font-medium text-fg-primary">
                    {RETENTION_CATEGORY_LABELS["incident_memories"]}
                  </div>
                  <p className="mt-1 max-w-md text-xs text-fg-muted">
                    {RETENTION_CATEGORY_DESCRIPTIONS["incident_memories"]}
                  </p>
                </td>
                <td className="px-4 py-3 text-right text-fg-primary tabular-nums">
                  {memoryStorage.row_count.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right text-fg-secondary tabular-nums">
                  {formatBytes(memoryStorage.estimated_bytes)}
                </td>
                <td className="px-4 py-3">
                  <Badge variant="closed">Never auto-deleted</Badge>
                </td>
                <td className="px-4 py-3 text-xs text-fg-muted">
                  Manual via{" "}
                  <Link
                    href="/dashboard/memories"
                    className="text-accent hover:underline"
                  >
                    /dashboard/memories
                  </Link>
                </td>
                <td className="px-4 py-3 text-right text-xs text-fg-muted">
                  —
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-fg-muted">
          Estimated sizes use conservative per-row averages — exact sizes
          require Postgres-only system tables. Use the trend over time, not the
          absolute number, to size storage.
        </p>
        <Button
          onClick={handleRunNow}
          loading={running}
          disabled={!canEdit}
          title="Trigger a one-shot pruner run for this org"
        >
          Run pruner now
        </Button>
      </div>

      {lastRun && (
        <div className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 text-xs">
          <p className="font-medium text-fg-primary">
            Last manual run · {new Date(lastRun.started_at).toLocaleString()}
          </p>
          <ul className="mt-2 space-y-1 text-fg-secondary">
            {lastRun.items.map((item) => (
              <li key={item.category}>
                <span className="font-mono">
                  {RETENTION_CATEGORY_LABELS[item.category] ?? item.category}
                </span>
                {": "}
                {item.skipped_reason
                  ? `skipped (${item.skipped_reason})`
                  : item.error
                    ? `error: ${item.error}`
                    : `deleted ${item.deleted_count} row${item.deleted_count === 1 ? "" : "s"}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}
