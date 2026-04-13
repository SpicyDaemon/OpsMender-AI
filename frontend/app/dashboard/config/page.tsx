"use client";

import { useCallback, useEffect, useState } from "react";
import { Save } from "lucide-react";
import {
  getConfig,
  listProviders,
  setModelConfig,
  updateConfig,
} from "@/lib/api";
import type {
  ConfigResponse,
  ModelConfigUpdate,
  ProviderModelsResponse,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, FormError } from "@/components/ui/Input";
import { PageSpinner } from "@/components/ui/Spinner";

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

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
      <div className="px-6 py-4 border-b border-gray-100">
        <h2 className="text-base font-semibold text-gray-900">{title}</h2>
        {description && (
          <p className="text-sm text-gray-500 mt-0.5">{description}</p>
        )}
      </div>
      <div className="px-6 py-5 space-y-4">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tier config section
// ---------------------------------------------------------------------------

function TierSection({
  config,
  onSaved,
}: {
  config: ConfigResponse;
  onSaved: () => void;
}) {
  const [tier, setTier] = useState(String(config.tier));
  const [logLevel, setLogLevel] = useState(config.logging_level);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await updateConfig({ tier: Number(tier), logging_level: logLevel });
      setSuccess(true);
      onSaved();
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
      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="cfg-tier">Global Tier</Label>
          <Select
            id="cfg-tier"
            value={tier}
            onChange={(e) => setTier(e.target.value)}
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
        <p className="text-sm text-gray-500 bg-gray-50 rounded-md px-3 py-2 font-mono">
          {config.audit_output}
        </p>
      </div>

      {error && <FormError message={error} />}
      {success && (
        <p className="text-sm text-green-600">Saved successfully.</p>
      )}

      <div className="flex justify-end">
        <Button onClick={handleSave} loading={saving}>
          <Save size={13} /> Save
        </Button>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Model config section
// ---------------------------------------------------------------------------

function ModelSection() {
  const [providers, setProviders] = useState<ProviderModelsResponse[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);

  const [form, setForm] = useState<ModelConfigUpdate>({
    provider: "anthropic",
    model_id: "",
    api_key_env_var: "",
    base_url: "",
    api_version: "",
    max_tokens: 4096,
    temperature: 0.0,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    listProviders()
      .then((res) => {
        setProviders(res.items);
        const first = res.items[0];
        if (first) {
          setForm((f) => ({
            ...f,
            provider: first.provider,
            model_id: first.default_model_id,
            api_key_env_var: first.default_api_key_env_var ?? "",
          }));
        }
      })
      .finally(() => setLoadingProviders(false));
  }, []);

  const selectedProvider = providers.find((p) => p.provider === form.provider);

  function setField<K extends keyof ModelConfigUpdate>(k: K, v: ModelConfigUpdate[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      await setModelConfig({
        ...form,
        api_key_env_var: form.api_key_env_var || undefined,
        base_url: form.base_url || undefined,
        api_version: form.api_version || undefined,
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (loadingProviders) return <PageSpinner />;

  return (
    <Section
      title="Model Provider"
      description="Configure the default LLM provider and model used for new sessions."
    >
      {/* Provider availability chips */}
      <div className="flex flex-wrap gap-2">
        {providers.map((p) => (
          <span
            key={p.provider}
            className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border font-medium ${
              p.available
                ? "border-green-200 bg-green-50 text-green-700"
                : "border-gray-200 bg-gray-50 text-gray-400"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${p.available ? "bg-green-500" : "bg-gray-300"}`}
            />
            {p.label}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="mp-provider">Provider</Label>
          <Select
            id="mp-provider"
            value={form.provider}
            onChange={(e) => {
              const p = providers.find((x) => x.provider === e.target.value);
              setForm((f) => ({
                ...f,
                provider: e.target.value,
                model_id: p?.default_model_id ?? "",
                api_key_env_var: p?.default_api_key_env_var ?? "",
                base_url: "",
                api_version: "",
              }));
            }}
          >
            {providers.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="mp-model">Model ID</Label>
          {selectedProvider && selectedProvider.models.length > 0 ? (
            <Select
              id="mp-model"
              value={form.model_id}
              onChange={(e) => setField("model_id", e.target.value)}
            >
              {selectedProvider.models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </Select>
          ) : (
            <Input
              id="mp-model"
              value={form.model_id}
              onChange={(e) => setField("model_id", e.target.value)}
              placeholder="model-id"
            />
          )}
        </div>
      </div>

      {selectedProvider?.requires_api_key && (
        <div>
          <Label htmlFor="mp-key">API Key Env Var</Label>
          <Input
            id="mp-key"
            value={form.api_key_env_var ?? ""}
            onChange={(e) => setField("api_key_env_var", e.target.value)}
            placeholder="ANTHROPIC_API_KEY"
          />
          <p className="mt-1 text-xs text-gray-400">
            Name of the environment variable holding the API key (not the key itself).
          </p>
        </div>
      )}

      {selectedProvider?.requires_base_url && (
        <div>
          <Label htmlFor="mp-url">Base URL</Label>
          <Input
            id="mp-url"
            value={form.base_url ?? ""}
            onChange={(e) => setField("base_url", e.target.value)}
            placeholder="http://localhost:11434"
          />
        </div>
      )}

      {selectedProvider?.requires_api_version && (
        <div>
          <Label htmlFor="mp-ver">API Version</Label>
          <Input
            id="mp-ver"
            value={form.api_version ?? ""}
            onChange={(e) => setField("api_version", e.target.value)}
            placeholder="2024-02-01"
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="mp-maxtok">Max Tokens</Label>
          <Input
            id="mp-maxtok"
            type="number"
            min={1}
            max={200000}
            value={form.max_tokens}
            onChange={(e) => setField("max_tokens", Number(e.target.value))}
          />
        </div>
        <div>
          <Label htmlFor="mp-temp">Temperature</Label>
          <Input
            id="mp-temp"
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
      {success && <p className="text-sm text-green-600">Model config saved.</p>}

      <div className="flex justify-end">
        <Button onClick={handleSave} loading={saving} disabled={!form.model_id}>
          <Save size={13} /> Save
        </Button>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// MCP servers section (read-only — edited via config.yaml)
// ---------------------------------------------------------------------------

function MCPSection({ config }: { config: ConfigResponse }) {
  return (
    <Section
      title="MCP Servers"
      description="Configured in config.yaml — restart the backend to apply changes."
    >
      {config.mcp_servers.length === 0 ? (
        <p className="text-sm text-gray-400">No MCP servers configured.</p>
      ) : (
        <div className="space-y-2">
          {config.mcp_servers.map((srv, i) => (
            <div
              key={i}
              className="rounded-lg bg-gray-50 border border-gray-200 px-4 py-3"
            >
              <pre className="text-xs text-gray-700 font-mono overflow-x-auto">
                {JSON.stringify(srv, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ConfigPage() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const c = await getConfig();
      setConfig(c);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading || !config) return <PageSpinner />;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Config</h1>
      <TierSection config={config} onSaved={load} />
      <ModelSection />
      <MCPSection config={config} />
    </div>
  );
}
