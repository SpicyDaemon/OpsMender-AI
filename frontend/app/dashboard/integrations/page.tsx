"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createIntegrationConnector,
  deleteIntegrationConnector,
  listIntegrationConnectors,
  listIntegrationKinds,
  testIntegrationConnector,
  updateIntegrationConnector,
} from "@/lib/api";
import type {
  IntegrationAuthType,
  IntegrationConnectorResponse,
  IntegrationConnectorUpsert,
  IntegrationKind,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea } from "@/components/ui/Input";

const EMPTY_OBJECT = "{}";
const INTEGRATION_HELP: Record<
  string,
  { base: string; auth: string; config: string }
> = {
  github: {
    base: "Hosted default: https://api.github.com. For Enterprise Server, enter its API base or instance root.",
    auth: 'PAT: {"token":"…"}. App: {"app_id":"…","installation_id":"…","private_key":"-----BEGIN PRIVATE KEY-----…"}',
    config: '{"owner":"acme","repo":"service","api_version":"2022-11-28"}',
  },
  gitlab: {
    base: "Hosted default: https://gitlab.com/api/v4. For self-managed, enter its API base or instance root.",
    auth: 'PAT: {"token":"…"}. OAuth: {"access_token":"…"}',
    config: '{"project":"group/project"}',
  },
  bitbucket: {
    base: "Cloud uses https://api.bitbucket.org/2.0 by default. For Data Center, enter the instance root or REST API base.",
    auth: 'Cloud API token: {"email":"admin@example.com","api_token":"…"}. OAuth: {"access_token":"…"}.',
    config:
      'Cloud: {"workspace":"acme","repo":"service"}. Data Center: {"edition":"data_center","project":"OPS","repo":"service"}.',
  },
  azure_devops: {
    base: "Leave blank for Azure DevOps Services, or enter the collection URL for a self-hosted deployment.",
    auth: 'PAT: {"token":"…"}. OAuth: {"access_token":"…"}',
    config:
      '{"organization":"acme","project":"Operations","repository":"service"}',
  },
  jira: {
    base: "Required: your Jira site or on-premises instance URL.",
    auth: 'Cloud API token: {"email":"admin@example.com","api_token":"…"}. OAuth: {"access_token":"…"}.',
    config:
      'Cloud: {"project_key":"OPS","issue_type":"Task"}. On-premises: {"edition":"on_prem","api_version":"2","project_key":"OPS"}.',
  },
  confluence: {
    base: "Required: your Confluence site or on-premises instance URL.",
    auth: 'Cloud API token: {"email":"admin@example.com","api_token":"…"}. OAuth: {"access_token":"…"}.',
    config:
      'Cloud: {"space_id":"12345"}. On-premises: {"edition":"on_prem","space_id":"OPS"}.',
  },
  servicenow: {
    base: "Required: your instance URL, such as https://acme.service-now.com.",
    auth: 'Basic: {"username":"…","password":"…"}. OAuth: {"access_token":"…"}',
    config: '{"table":"incident"}',
  },
  linear: {
    base: "Uses https://api.linear.app/graphql by default.",
    auth: 'API key: {"api_key":"…"}. OAuth: {"access_token":"…"}',
    config: '{"team_id":"…"}',
  },
  notion: {
    base: "Uses https://api.notion.com/v1 by default.",
    auth: 'Integration token: {"api_key":"…"}. OAuth: {"access_token":"…"}',
    config: '{"parent_page_id":"…","notion_version":"2026-03-11"}',
  },
  kubernetes: {
    base: "Required: the Kubernetes API server URL, such as https://cluster.example.com:6443.",
    auth: 'Service account: {"token":"…","ca_cert":"-----BEGIN CERTIFICATE-----…"}. Custom headers: {"headers":{"Authorization":"…"}}.',
    config: '{"namespace":"production","verify_tls":true}',
  },
};

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value || EMPTY_OBJECT);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function statusClass(status: string): string {
  if (status === "healthy")
    return "bg-status-low-bg text-status-low border-status-low-border";
  if (status === "error")
    return "bg-status-critical-bg text-status-critical border-status-critical-border";
  return "bg-status-neutral-bg text-status-neutral border-status-neutral-border";
}

export default function IntegrationsPage() {
  const [kinds, setKinds] = useState<IntegrationKind[]>([]);
  const [connectors, setConnectors] = useState<IntegrationConnectorResponse[]>(
    [],
  );
  const [editing, setEditing] = useState<IntegrationConnectorResponse | null>(
    null,
  );
  const [name, setName] = useState("");
  const [kind, setKind] = useState("custom");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState<IntegrationAuthType>("pat");
  const [authJson, setAuthJson] = useState(EMPTY_OBJECT);
  const [configJson, setConfigJson] = useState(EMPTY_OBJECT);
  const [enabled, setEnabled] = useState(true);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [kindResponse, connectorResponse] = await Promise.all([
      listIntegrationKinds(),
      listIntegrationConnectors(),
    ]);
    setKinds(kindResponse.items);
    setConnectors(connectorResponse.items);
  }, []);

  useEffect(() => {
    reload().catch((error) => {
      setNotice(
        error instanceof Error ? error.message : "Unable to load integrations.",
      );
    });
  }, [reload]);

  const selectedKind = useMemo(
    () => kinds.find((item) => item.kind === kind),
    [kind, kinds],
  );
  const integrationHelp = INTEGRATION_HELP[kind];

  function resetForm() {
    setEditing(null);
    setName("");
    setKind("custom");
    setBaseUrl("");
    setAuthType("pat");
    setAuthJson(EMPTY_OBJECT);
    setConfigJson(EMPTY_OBJECT);
    setEnabled(true);
  }

  function beginEdit(connector: IntegrationConnectorResponse) {
    setEditing(connector);
    setName(connector.name);
    setKind(connector.kind);
    setBaseUrl(connector.base_url ?? "");
    setAuthType(connector.auth_type);
    setAuthJson(EMPTY_OBJECT);
    setConfigJson(JSON.stringify(connector.config ?? {}, null, 2));
    setEnabled(connector.is_enabled);
    setNotice(
      connector.has_auth
        ? "Credentials are stored. Leave Credentials JSON as {} to keep them."
        : "",
    );
  }

  async function save() {
    setBusy(true);
    setNotice("");
    try {
      const auth = parseObject(authJson, "Credentials");
      const config = parseObject(configJson, "Configuration");
      const payload: IntegrationConnectorUpsert = {
        kind,
        name,
        base_url: baseUrl || null,
        auth_type: authType,
        config,
        is_enabled: enabled,
      };
      if (Object.keys(auth).length > 0) payload.auth = auth;
      if (editing) {
        await updateIntegrationConnector(editing.id, payload);
        setNotice("Integration updated.");
      } else {
        await createIntegrationConnector(payload);
        setNotice("Integration created.");
      }
      resetForm();
      await reload();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(connector: IntegrationConnectorResponse) {
    await updateIntegrationConnector(connector.id, {
      kind: connector.kind,
      name: connector.name,
      base_url: connector.base_url,
      auth_type: connector.auth_type,
      config: connector.config,
      is_enabled: !connector.is_enabled,
    });
    await reload();
  }

  async function test(connector: IntegrationConnectorResponse) {
    const result = await testIntegrationConnector(connector.id);
    setNotice(result.detail);
    await reload();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-fg-primary">Integrations</h1>
        <p className="mt-1 text-sm text-fg-secondary">
          Connect source control, ticketing, documentation, observability, and
          infrastructure systems. Credentials are encrypted and never returned
          by the API.
        </p>
      </div>

      <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold text-fg-primary">
            {editing ? `Edit ${editing.name}` : "Add integration"}
          </h2>
          {editing && (
            <Button variant="secondary" size="sm" onClick={resetForm}>
              Cancel
            </Button>
          )}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="integration-name">Name</Label>
            <Input
              id="integration-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="integration-kind">Kind</Label>
            <Select
              id="integration-kind"
              value={kind}
              onChange={(event) => {
                const next = event.target.value;
                setKind(next);
                const definition = kinds.find((item) => item.kind === next);
                if (definition?.auth_types[0])
                  setAuthType(definition.auth_types[0]);
              }}
            >
              {kinds.map((item) => (
                <option key={item.kind} value={item.kind}>
                  {item.label}
                </option>
              ))}
            </Select>
            {selectedKind && !selectedKind.adapter_available && (
              <p className="mt-1 text-xs text-fg-muted">
                Configuration can be saved; the adapter lands in its Wave 1
                phase.
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="integration-base-url">Base URL</Label>
            <Input
              id="integration-base-url"
              placeholder="https://service.example.com"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            {integrationHelp && (
              <p className="mt-1 text-xs text-fg-muted">
                {integrationHelp.base}
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="integration-auth-type">Authentication</Label>
            <Select
              id="integration-auth-type"
              value={authType}
              onChange={(event) =>
                setAuthType(event.target.value as IntegrationAuthType)
              }
            >
              {(selectedKind?.auth_types ?? ["pat"]).map((item) => (
                <option key={item} value={item}>
                  {item.replaceAll("_", " ")}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="integration-auth">Credentials JSON</Label>
            <Textarea
              id="integration-auth"
              rows={6}
              value={authJson}
              onChange={(event) => setAuthJson(event.target.value)}
              spellCheck={false}
            />
            <p className="mt-1 text-xs text-fg-muted">
              Example: {`{"token":"…"}`}. Saved values are write-only.
            </p>
            {integrationHelp && (
              <p className="mt-1 text-xs text-fg-muted">
                {integrationHelp.auth}
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="integration-config">Configuration JSON</Label>
            <Textarea
              id="integration-config"
              rows={6}
              value={configJson}
              onChange={(event) => setConfigJson(event.target.value)}
              spellCheck={false}
            />
            <p className="mt-1 text-xs text-fg-muted">
              Repository, project, organization, or adapter-specific options.
            </p>
            {integrationHelp && (
              <p className="mt-1 text-xs text-fg-muted">
                Example: {integrationHelp.config}
              </p>
            )}
          </div>
        </div>
        {selectedKind && selectedKind.capabilities.length > 0 && (
          <div className="rounded-lg border border-border-subtle bg-bg-elevated p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
              Available capabilities
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {selectedKind.capabilities.map((capability) => (
                <span
                  key={capability.action}
                  className="rounded-full border border-border-subtle px-2 py-1 text-xs text-fg-secondary"
                >
                  {capability.action}
                  {capability.mutating ? " · approval-gated write" : " · read"}
                </span>
              ))}
            </div>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Enabled for operator use and tier-governed agent tools
        </label>
        <Button onClick={save} loading={busy} disabled={!name || !kind}>
          {editing ? "Save integration" : "Create integration"}
        </Button>
      </section>

      {notice && (
        <p className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3 text-sm text-fg-secondary">
          {notice}
        </p>
      )}

      <section className="space-y-3">
        <h2 className="font-semibold text-fg-primary">
          Configured integrations
        </h2>
        {connectors.length === 0 && (
          <div className="rounded-xl border border-dashed border-border-subtle p-8 text-center text-sm text-fg-muted">
            No integrations configured yet.
          </div>
        )}
        {connectors.map((connector) => (
          <article
            key={connector.id}
            className="rounded-xl border border-border-subtle bg-bg-panel p-5"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold text-fg-primary">
                    {connector.name}
                  </h3>
                  <span className="rounded-full border border-border-subtle px-2 py-0.5 text-xs text-fg-secondary">
                    {connector.kind}
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs ${statusClass(connector.status)}`}
                  >
                    {connector.status}
                  </span>
                </div>
                <p className="mt-1 text-sm text-fg-secondary">
                  {connector.base_url || "Provider default endpoint"}
                </p>
                <p className="mt-1 text-xs text-fg-muted">
                  {connector.has_auth
                    ? `Credentials configured (${connector.auth_keys.join(", ") || "encrypted"})`
                    : "No credentials stored"}
                  {connector.last_checked_at
                    ? ` · checked ${new Date(connector.last_checked_at).toLocaleString()}`
                    : ""}
                </p>
                {connector.last_error && (
                  <p className="mt-1 text-xs text-status-critical">
                    {connector.last_error}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => test(connector)}
                >
                  Test
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => beginEdit(connector)}
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => toggle(connector)}
                >
                  {connector.is_enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={async () => {
                    if (!window.confirm(`Delete ${connector.name}?`)) return;
                    await deleteIntegrationConnector(connector.id);
                    await reload();
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
