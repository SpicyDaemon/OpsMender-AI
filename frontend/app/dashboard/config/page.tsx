"use client";

import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Save, Star, Trash2 } from "lucide-react";
import {
  createModelConfig,
  deleteModelConfig,
  getConfig,
  listModelConfigs,
  listProviders,
  setDefaultModelConfig,
  updateConfig,
  updateModelConfigById,
} from "@/lib/api";
import type {
  ConfigResponse,
  ModelConfigResponse,
  ModelConfigUpdate,
  ProviderModelsResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";
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

function MCPSection({ config }: { config: ConfigResponse }) {
  return (
    <Section
      title="MCP Servers"
      description="Loaded from the active runtime config. Full MCP server management will move here in Sprint 12."
    >
      {config.mcp_servers.length === 0 ? (
        <p className="text-sm text-gray-400">No MCP servers configured.</p>
      ) : (
        <div className="space-y-2">
          {config.mcp_servers.map((server, index) => (
            <div
              key={index}
              className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3"
            >
              <pre className="overflow-x-auto font-mono text-xs text-gray-700">
                {JSON.stringify(server, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

export default function ConfigPage() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";

  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [providers, setProviders] = useState<ProviderModelsResponse[]>([]);
  const [modelConfigs, setModelConfigs] = useState<ModelConfigResponse[]>([]);
  const [loading, setLoading] = useState(true);

  const loadPageData = useCallback(async () => {
    const [runtimeConfig, providerList, savedConfigs] = await Promise.all([
      getConfig(),
      listProviders(),
      listModelConfigs(),
    ]);
    setConfig(runtimeConfig);
    setProviders(providerList.items);
    setModelConfigs(savedConfigs.items);
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
          Manage runtime defaults, saved model profiles, and upcoming operator self-service tools.
        </p>
      </div>

      <TierSection config={config} onSaved={loadPageData} canEdit={canEdit} />
      <ModelSection
        providers={providers}
        configs={modelConfigs}
        onReload={loadPageData}
        canEdit={canEdit}
      />
      <MCPSection config={config} />
    </div>
  );
}
