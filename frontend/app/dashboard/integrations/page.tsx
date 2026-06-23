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
  IntegrationField,
  IntegrationKind,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea } from "@/components/ui/Input";

// Friendly Title-Case labels for authentication methods (acronyms stay upper).
const AUTH_TYPE_LABELS: Record<string, string> = {
  none: "None",
  pat: "Personal Access Token",
  api_key: "API Key",
  basic: "Basic",
  custom: "Custom",
  oauth: "OAuth",
  app: "App",
};

function authTypeLabel(value: string): string {
  return (
    AUTH_TYPE_LABELS[value] ??
    value
      .replaceAll("_", " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
  );
}
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
  gitea: {
    base: "Required: your Gitea instance URL or API v1 base.",
    auth: 'Personal access token: {"token":"…"}',
    config: '{"owner":"acme","repo":"service"}',
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
      'Cloud: {"project_key":"OPS","issue_type":"Task","ticket_sync_enabled":true}. Store webhook_secret in Credentials JSON.',
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
    config:
      '{"table":"incident","ticket_sync_enabled":true}. Store webhook_token in Credentials JSON.',
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
  google_docs: {
    base: "Uses the Google Docs and Drive APIs.",
    auth: 'OAuth: {"access_token":"…"}. Service account (Custom): {"client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----…","delegated_user":"optional@example.com"}',
    config: "Share documents with the service account, or configure domain-wide delegation.",
  },
  kubernetes: {
    base: "Required: the Kubernetes API server URL, such as https://cluster.example.com:6443.",
    auth: 'Service account: {"token":"…","ca_cert":"-----BEGIN CERTIFICATE-----…"}. Custom headers: {"headers":{"Authorization":"…"}}.',
    config: '{"namespace":"production","verify_tls":true}',
  },
  jenkins: {
    base: "Required: the Jenkins controller URL.",
    auth: 'API token/basic: {"username":"…","api_token":"…"}',
    config: '{"job":"folder/service"}',
  },
  circleci: {
    base: "Uses https://circleci.com/api/v2 by default. Enter a CircleCI Server API base when self-hosted.",
    auth: 'API token: {"api_key":"…"}',
    config: '{"project_slug":"gh/acme/service"}',
  },
  azure_pipelines: {
    base: "Leave blank for Azure DevOps Services, or enter the collection URL for a self-hosted deployment.",
    auth: 'PAT: {"token":"…"}. OAuth: {"access_token":"…"}',
    config: '{"organization":"acme","project":"Operations"}',
  },
  terraform_cloud: {
    base: "Uses https://app.terraform.io/api/v2 by default. Enter a Terraform Enterprise API v2 base when self-hosted.",
    auth: 'User or team API token: {"api_key":"…"}',
    config: '{"organization":"acme","workspace_id":"ws-…"}',
  },
  argocd: {
    base: "Required: the Argo CD server URL.",
    auth: 'Token: {"token":"…"}. OAuth: {"access_token":"…"}',
    config: '{"application":"production-service"}',
  },
  ansible: {
    base: "Required: the AWX or Ansible Automation Controller URL.",
    auth: 'Token: {"token":"…"}. Basic: {"username":"…","password":"…"}',
    config: "No extra config required.",
  },
  statuspage: {
    base: "Uses https://api.statuspage.io/v1 by default.",
    auth: 'API token: {"api_key":"…"}',
    config: '{"page_id":"…"}',
  },
  zendesk: {
    base: "Required: your subdomain URL, such as https://acme.zendesk.com.",
    auth: 'API token: {"email":"agent@acme.com","api_token":"…"}. OAuth: {"access_token":"…"}.',
    config: "No extra config required.",
  },
  freshservice: {
    base: "Required: your portal URL, such as https://acme.freshservice.com.",
    auth: 'API key: {"api_key":"…"}.',
    config: "No extra config required.",
  },
  asana: {
    base: "Uses https://app.asana.com/api/1.0 by default.",
    auth: 'PAT: {"token":"…"}. OAuth: {"access_token":"…"}.',
    config: '{"project_id":"…"} (default project for create/list tasks).',
  },
};

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function fieldValue(
  values: Record<string, string>,
  field: IntegrationField,
): string {
  return Object.prototype.hasOwnProperty.call(values, field.name)
    ? values[field.name]
    : formatFieldValue(field.default);
}

function parseFieldValue(
  field: IntegrationField,
  raw: string,
): unknown | undefined {
  const value = raw.trim();
  if (!value) return undefined;
  if (field.kind === "number") {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${field.label} must be a number.`);
    }
    return parsed;
  }
  if (typeof field.default === "boolean") {
    return value === "true";
  }
  if (
    field.default !== null &&
    typeof field.default === "object" &&
    !Array.isArray(field.default)
  ) {
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(`${field.label} must be a JSON object.`);
    }
    return parsed as Record<string, unknown>;
  }
  return raw;
}

function valuesForFields(
  fields: IntegrationField[],
  source: Record<string, unknown>,
): Record<string, string> {
  return Object.fromEntries(
    fields
      .filter((field) =>
        Object.prototype.hasOwnProperty.call(source, field.name),
      )
      .map((field) => [field.name, formatFieldValue(source[field.name])]),
  );
}

interface AdditionalVariable {
  id: string;
  key: string;
  value: string;
  saved?: boolean;
  originalValue?: unknown;
}

let variableSequence = 0;

function additionalVariable(
  key = "",
  value = "",
  options: { saved?: boolean; originalValue?: unknown } = {},
): AdditionalVariable {
  variableSequence += 1;
  return {
    id: `integration-variable-${variableSequence}`,
    key,
    value,
    ...options,
  };
}

function additionalCredentialRows(
  connector: IntegrationConnectorResponse | null,
  fields: IntegrationField[],
): AdditionalVariable[] {
  if (!connector) return [];
  const covered = new Set(fields.map((field) => field.name));
  return connector.auth_keys
    .filter((key) => !covered.has(key))
    .map((key) => additionalVariable(key, "", { saved: true }));
}

function additionalConfigRows(
  source: Record<string, unknown>,
  fields: IntegrationField[],
): AdditionalVariable[] {
  const covered = new Set(fields.map((field) => field.name));
  return Object.entries(source)
    .filter(([key]) => !covered.has(key))
    .map(([key, value]) =>
      additionalVariable(key, formatFieldValue(value), {
        originalValue: value,
      }),
    );
}

function parseAdditionalValue(row: AdditionalVariable): unknown | undefined {
  if (!row.value.trim()) {
    return row.originalValue === "" ? "" : undefined;
  }
  if (
    row.originalValue !== undefined &&
    row.value === formatFieldValue(row.originalValue)
  ) {
    return row.originalValue;
  }
  try {
    return JSON.parse(row.value);
  } catch {
    return row.value;
  }
}

function AdditionalVariables({
  group,
  rows,
  onChange,
  onRemove,
  onAdd,
}: {
  group: "credentials" | "config";
  rows: AdditionalVariable[];
  onChange: (id: string, patch: Partial<AdditionalVariable>) => void;
  onRemove: (row: AdditionalVariable) => void;
  onAdd: () => void;
}) {
  const label = group === "credentials" ? "credential" : "config";
  return (
    <div className="border-t border-border-subtle pt-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-fg-primary">
            Additional variables
          </h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Keys not covered by this integration&apos;s schema.
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" onClick={onAdd}>
          Add variable
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="mt-3 text-xs text-fg-muted">
          No additional {label} variables.
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {rows.map((row, index) => (
            <div
              key={row.id}
              className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
            >
              <Input
                aria-label={`Additional ${label} key ${index + 1}`}
                value={row.key}
                placeholder="key"
                disabled={row.saved}
                onChange={(event) =>
                  onChange(row.id, { key: event.target.value })
                }
              />
              <Input
                aria-label={`Additional ${label} value ${index + 1}`}
                type={group === "credentials" ? "password" : "text"}
                value={row.value}
                placeholder={
                  row.saved ? "Saved — leave blank to keep" : "value"
                }
                autoComplete="off"
                onChange={(event) =>
                  onChange(row.id, { value: event.target.value })
                }
              />
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => onRemove(row)}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StructuredField({
  field,
  value,
  saved,
  onChange,
}: {
  field: IntegrationField;
  value: string;
  saved?: boolean;
  onChange: (value: string) => void;
}) {
  const id = `integration-${field.group}-${field.name}`;
  const placeholder =
    saved && field.group === "credentials"
      ? "Saved — leave blank to keep"
      : (field.placeholder ?? "");
  return (
    <div>
      <Label htmlFor={id}>
        {field.label}
        {field.required ? " *" : ""}
      </Label>
      {field.kind === "textarea" ? (
        <Textarea
          id={id}
          rows={field.default && typeof field.default === "object" ? 5 : 4}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
        />
      ) : field.kind === "select" ? (
        <Select
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {!field.required && !value ? <option value="">Not set</option> : null}
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      ) : (
        <Input
          id={id}
          type={field.kind === "secret" ? "password" : field.kind}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          autoComplete="off"
        />
      )}
      {(field.helper || field.doc_url || saved) && (
        <p className="mt-1 text-xs text-fg-muted">
          {saved ? "A value is already stored. Enter a new value to replace it. " : ""}
          {field.helper}
          {field.doc_url ? (
            <>
              {" "}
              <a
                href={field.doc_url}
                target="_blank"
                rel="noreferrer"
                className="text-accent hover:underline"
              >
                Where do I get this?
              </a>
            </>
          ) : null}
        </p>
      )}
    </div>
  );
}

function statusClass(status: string): string {
  if (status === "healthy")
    return "bg-status-low-bg text-status-low border-status-low-border";
  if (status === "error")
    return "bg-status-critical-bg text-status-critical border-status-critical-border";
  return "bg-status-neutral-bg text-status-neutral border-status-neutral-border";
}

const DEFAULT_TICKET_STATUS_MAP: Record<string, Record<string, string>> = {
  jira: {
    open: "To Do",
    in_progress: "In Progress",
    resolved: "Done",
  },
  servicenow: {
    open: "1",
    in_progress: "2",
    resolved: "6",
  },
};

function TicketSyncPanel({
  connector,
  onSaved,
}: {
  connector: IntegrationConnectorResponse;
  onSaved: () => Promise<void>;
}) {
  const defaults = DEFAULT_TICKET_STATUS_MAP[connector.kind] ?? {};
  const configuredMap =
    connector.config.status_map &&
    typeof connector.config.status_map === "object" &&
    !Array.isArray(connector.config.status_map)
      ? (connector.config.status_map as Record<string, unknown>)
      : {};
  const [enabled, setEnabled] = useState(
    Boolean(connector.config.ticket_sync_enabled),
  );
  const [statusMap, setStatusMap] = useState<Record<string, string>>({
    open: String(configuredMap.open ?? defaults.open ?? ""),
    in_progress: String(configuredMap.in_progress ?? defaults.in_progress ?? ""),
    resolved: String(configuredMap.resolved ?? defaults.resolved ?? ""),
  });
  const [saving, setSaving] = useState(false);

  async function saveSyncSettings() {
    setSaving(true);
    try {
      await updateIntegrationConnector(connector.id, {
        kind: connector.kind,
        name: connector.name,
        base_url: connector.base_url,
        auth_type: connector.auth_type,
        config: {
          ...connector.config,
          ticket_sync_enabled: enabled,
          status_map: statusMap,
        },
        is_enabled: connector.is_enabled,
      });
      await onSaved();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-border-subtle bg-bg-elevated p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-fg-primary">
            Bi-directional ticket sync
          </h4>
          <p className="mt-1 text-xs text-fg-secondary">
            Outbound incident changes update linked tickets. Signed inbound
            webhooks update the incident without creating a sync loop.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Sync enabled
        </label>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {(["open", "in_progress", "resolved"] as const).map((status) => (
          <div key={status}>
            <Label htmlFor={`sync-${connector.id}-${status}`}>
              {status.replace("_", " ")}
            </Label>
            <Input
              id={`sync-${connector.id}-${status}`}
              value={statusMap[status]}
              onChange={(event) =>
                setStatusMap({ ...statusMap, [status]: event.target.value })
              }
            />
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-md bg-bg-muted p-3 text-xs text-fg-secondary">
        <p className="font-medium text-fg-primary">Inbound webhook URL</p>
        <code className="mt-1 block break-all font-mono">
          /webhooks/ticket-sync/{connector.id}
          {connector.kind === "servicenow" ? "?webhook_token=…" : ""}
        </code>
        <p className="mt-2">
          {connector.kind === "jira"
            ? "Jira must send X-Hub-Signature (HMAC-SHA256) using the webhook_secret stored in Credentials JSON."
            : "ServiceNow must send the webhook_token query parameter matching the encrypted credential."}
        </p>
      </div>
      <Button
        className="mt-4"
        size="sm"
        onClick={saveSyncSettings}
        loading={saving}
      >
        Save sync settings
      </Button>
    </div>
  );
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
  const [credentialValues, setCredentialValues] = useState<
    Record<string, string>
  >({});
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [additionalCredentials, setAdditionalCredentials] = useState<
    AdditionalVariable[]
  >([]);
  const [additionalConfig, setAdditionalConfig] = useState<
    AdditionalVariable[]
  >([]);
  const [removedCredentialKeys, setRemovedCredentialKeys] = useState<string[]>(
    [],
  );
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
  const credentialFields = useMemo(
    () => selectedKind?.credential_fields?.[authType] ?? [],
    [authType, selectedKind],
  );
  const configFields = selectedKind?.config_fields ?? [];
  const integrationHelp = INTEGRATION_HELP[kind];

  function resetForm() {
    setEditing(null);
    setName("");
    setKind("custom");
    setBaseUrl("");
    setAuthType("pat");
    setCredentialValues({});
    setConfigValues({});
    setAdditionalCredentials([]);
    setAdditionalConfig([]);
    setRemovedCredentialKeys([]);
    setEnabled(true);
  }

  function beginEdit(connector: IntegrationConnectorResponse) {
    setEditing(connector);
    setName(connector.name);
    setKind(connector.kind);
    setBaseUrl(connector.base_url ?? "");
    setAuthType(connector.auth_type);
    setCredentialValues({});
    const definition = kinds.find((item) => item.kind === connector.kind);
    const fields =
      definition?.credential_fields?.[connector.auth_type] ?? [];
    setConfigValues(
      valuesForFields(definition?.config_fields ?? [], connector.config ?? {}),
    );
    setAdditionalCredentials(additionalCredentialRows(connector, fields));
    setAdditionalConfig(
      additionalConfigRows(
        connector.config ?? {},
        definition?.config_fields ?? [],
      ),
    );
    setRemovedCredentialKeys([]);
    setEnabled(connector.is_enabled);
    setNotice(
      connector.has_auth
        ? "Credentials are stored. Leave saved credential fields blank to keep them."
        : "",
    );
  }

  async function save() {
    setBusy(true);
    setNotice("");
    try {
      const missingCredential = credentialFields.find(
        (field) =>
          field.required &&
          !fieldValue(credentialValues, field).trim() &&
          !(editing?.auth_keys ?? []).includes(field.name),
      );
      if (missingCredential) {
        throw new Error(`${missingCredential.label} is required.`);
      }
      const missingConfig = configFields.find(
        (field) => field.required && !fieldValue(configValues, field).trim(),
      );
      if (missingConfig) {
        throw new Error(`${missingConfig.label} is required.`);
      }

      const auth: Record<string, unknown> = {};
      for (const field of credentialFields) {
        const value = parseFieldValue(
          field,
          fieldValue(credentialValues, field),
        );
        if (value !== undefined) auth[field.name] = value;
      }
      const credentialKeys = new Set(credentialFields.map((field) => field.name));
      for (const row of additionalCredentials) {
        const key = row.key.trim();
        if (!key && !row.value.trim()) continue;
        if (!key) throw new Error("Every additional credential needs a key.");
        if (credentialKeys.has(key)) {
          throw new Error(`Credential key '${key}' is already a structured field.`);
        }
        if (credentialKeys.has(`additional:${key}`)) {
          throw new Error(`Credential key '${key}' is duplicated.`);
        }
        credentialKeys.add(`additional:${key}`);
        const value = parseAdditionalValue(row);
        if (value !== undefined) auth[key] = value;
        else if (!row.saved) {
          throw new Error(`Additional credential '${key}' needs a value.`);
        }
      }
      for (const key of removedCredentialKeys) auth[key] = null;

      const config: Record<string, unknown> = {};
      for (const field of configFields) {
        const value = parseFieldValue(field, fieldValue(configValues, field));
        if (value === undefined) delete config[field.name];
        else config[field.name] = value;
      }
      const configKeys = new Set(configFields.map((field) => field.name));
      for (const row of additionalConfig) {
        const key = row.key.trim();
        if (!key && !row.value.trim()) continue;
        if (!key) throw new Error("Every additional config variable needs a key.");
        if (configKeys.has(key)) {
          throw new Error(`Config key '${key}' is already a structured field.`);
        }
        if (configKeys.has(`additional:${key}`)) {
          throw new Error(`Config key '${key}' is duplicated.`);
        }
        configKeys.add(`additional:${key}`);
        const value = parseAdditionalValue(row);
        if (value === undefined) {
          throw new Error(`Additional config variable '${key}' needs a value.`);
        }
        config[key] = value;
      }
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
                if (definition?.auth_types[0]) {
                  setAuthType(definition.auth_types[0]);
                }
                setCredentialValues({});
                const nextCredentialFields =
                  definition?.credential_fields?.[definition.auth_types[0]] ??
                  [];
                setAdditionalCredentials(
                  additionalCredentialRows(editing, nextCredentialFields),
                );
                setConfigValues(
                  valuesForFields(
                    definition?.config_fields ?? [],
                    editing?.config ?? {},
                  ),
                );
                setAdditionalConfig(
                  additionalConfigRows(
                    editing?.config ?? {},
                    definition?.config_fields ?? [],
                  ),
                );
                setRemovedCredentialKeys([]);
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
          {selectedKind?.supports_base_url ? (
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
          ) : null}
          <div>
            <Label htmlFor="integration-auth-type">Authentication</Label>
            <Select
              id="integration-auth-type"
              value={authType}
              onChange={(event) => {
                const nextAuthType = event.target.value as IntegrationAuthType;
                setAuthType(nextAuthType);
                setCredentialValues({});
                setAdditionalCredentials(
                  additionalCredentialRows(
                    editing,
                    selectedKind?.credential_fields?.[nextAuthType] ?? [],
                  ),
                );
                setRemovedCredentialKeys([]);
              }}
            >
              {(selectedKind?.auth_types ?? ["pat"]).map((item) => (
                <option key={item} value={item}>
                  {authTypeLabel(item)}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          <fieldset className="space-y-4 rounded-lg border border-border-subtle bg-bg-elevated p-4">
            <legend className="px-1 text-sm font-semibold text-fg-primary">
              Credentials
            </legend>
            {credentialFields.length === 0 ? (
              <p className="text-sm text-fg-muted">
                No credentials are required for this authentication method.
              </p>
            ) : (
              credentialFields.map((field) => (
                <StructuredField
                  key={field.name}
                  field={field}
                  value={fieldValue(credentialValues, field)}
                  saved={Boolean(editing?.auth_keys.includes(field.name))}
                  onChange={(value) =>
                    setCredentialValues((current) => ({
                      ...current,
                      [field.name]: value,
                    }))
                  }
                />
              ))
            )}
            <p className="text-xs text-fg-muted">
              Credential values are encrypted and never returned by the API.
            </p>
            <AdditionalVariables
              group="credentials"
              rows={additionalCredentials}
              onAdd={() =>
                setAdditionalCredentials((rows) => [
                  ...rows,
                  additionalVariable(),
                ])
              }
              onChange={(id, patch) =>
                setAdditionalCredentials((rows) =>
                  rows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
                )
              }
              onRemove={(target) => {
                setAdditionalCredentials((rows) =>
                  rows.filter((row) => row.id !== target.id),
                );
                if (target.saved) {
                  setRemovedCredentialKeys((keys) => [
                    ...new Set([...keys, target.key]),
                  ]);
                }
              }}
            />
          </fieldset>
          <fieldset className="space-y-4 rounded-lg border border-border-subtle bg-bg-elevated p-4">
            <legend className="px-1 text-sm font-semibold text-fg-primary">
              Configuration
            </legend>
            {configFields.length === 0 ? (
              <p className="text-sm text-fg-muted">
                No adapter-specific configuration is required.
              </p>
            ) : (
              configFields.map((field) => (
                <StructuredField
                  key={field.name}
                  field={field}
                  value={fieldValue(configValues, field)}
                  onChange={(value) =>
                    setConfigValues((current) => ({
                      ...current,
                      [field.name]: value,
                    }))
                  }
                />
              ))
            )}
            <AdditionalVariables
              group="config"
              rows={additionalConfig}
              onAdd={() =>
                setAdditionalConfig((rows) => [
                  ...rows,
                  additionalVariable(),
                ])
              }
              onChange={(id, patch) =>
                setAdditionalConfig((rows) =>
                  rows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
                )
              }
              onRemove={(target) =>
                setAdditionalConfig((rows) =>
                  rows.filter((row) => row.id !== target.id),
                )
              }
            />
          </fieldset>
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
                  {capability.always_requires_approval
                    ? " · always approval"
                    : capability.mutating
                      ? " · approval-gated write"
                      : " · read"}
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
            {connector.kind === "jira" || connector.kind === "servicenow" ? (
              <TicketSyncPanel connector={connector} onSaved={reload} />
            ) : null}
          </article>
        ))}
      </section>
    </div>
  );
}
