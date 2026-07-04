"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
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
import { IconSelect } from "@/components/ui/IconSelect";
import { integrationKindIcon } from "@/lib/brand-icons";
import { formatDateTime } from "@/lib/formatDate";

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
  /** Encrypted/write-only (stored with the credentials) vs plaintext config. */
  secret: boolean;
  saved?: boolean;
  originalValue?: unknown;
}

let variableSequence = 0;

function additionalVariable(
  key = "",
  value = "",
  options: { secret?: boolean; saved?: boolean; originalValue?: unknown } = {},
): AdditionalVariable {
  variableSequence += 1;
  const { secret = false, ...rest } = options;
  return {
    id: `integration-variable-${variableSequence}`,
    key,
    value,
    secret,
    ...rest,
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
    .map((key) => additionalVariable(key, "", { secret: true, saved: true }));
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
        secret: false,
        originalValue: value,
      }),
    );
}

/**
 * The single merged additional-variables list: encrypted credential extras
 * first (write-only, marked Secret), then plaintext config extras. Replaces
 * the old split credential/config repeaters.
 */
function mergedAdditionalRows(
  connector: IntegrationConnectorResponse | null,
  credentialFields: IntegrationField[],
  configFields: IntegrationField[],
): AdditionalVariable[] {
  return [
    ...additionalCredentialRows(connector, credentialFields),
    ...additionalConfigRows(connector?.config ?? {}, configFields),
  ];
}

function parseAdditionalValue(row: AdditionalVariable): unknown | undefined {
  if (!row.value.trim()) {
    return Object.prototype.hasOwnProperty.call(row, "originalValue")
      ? row.originalValue
      : undefined;
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

function defaultAuthTypeForKind(
  definition: IntegrationKind | undefined,
): IntegrationAuthType {
  if (definition?.kind === "custom" && definition.auth_types.includes("pat")) {
    return "pat";
  }
  return (definition?.auth_types[0] ?? "pat") as IntegrationAuthType;
}

function AdditionalVariables({
  rows,
  onChange,
  onRemove,
  onAdd,
}: {
  rows: AdditionalVariable[];
  onChange: (id: string, patch: Partial<AdditionalVariable>) => void;
  onRemove: (row: AdditionalVariable) => void;
  onAdd: () => void;
}) {
  return (
    <fieldset className="space-y-4 rounded-lg border border-border-subtle bg-bg-elevated p-4">
      <legend className="px-1 text-sm font-semibold text-fg-primary">
        Additional variables
      </legend>
      <p className="text-xs text-fg-muted">
        Extra keys this integration&apos;s structured fields don&apos;t cover.
        Toggle <strong className="text-fg-secondary">Secret</strong> to store a
        value encrypted and write-only (alongside the credentials); leave it off
        to store plaintext configuration.
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-fg-muted">No additional variables.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((row, index) => (
            <div
              key={row.id}
              className="grid items-center gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto]"
            >
              <Input
                aria-label={`Additional variable key ${index + 1}`}
                value={row.key}
                placeholder="key"
                disabled={row.saved}
                onChange={(event) =>
                  onChange(row.id, { key: event.target.value })
                }
              />
              <Input
                aria-label={`Additional variable value ${index + 1}`}
                type={row.secret ? "password" : "text"}
                value={row.value}
                placeholder={
                  row.saved ? "Saved — leave blank to keep" : "value"
                }
                autoComplete="off"
                onChange={(event) =>
                  onChange(row.id, { value: event.target.value })
                }
              />
              <label
                className="inline-flex items-center gap-1.5 text-xs text-fg-secondary"
                title={
                  row.saved
                    ? "Storage can't change for a saved key — remove and re-add it"
                    : "Store this value encrypted (write-only)"
                }
              >
                <input
                  type="checkbox"
                  aria-label={`Additional variable secret ${index + 1}`}
                  checked={row.secret}
                  disabled={row.saved}
                  onChange={(event) =>
                    onChange(row.id, { secret: event.target.checked })
                  }
                />
                Secret
              </label>
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
      <Button type="button" size="sm" variant="secondary" onClick={onAdd}>
        Add variable
      </Button>
    </fieldset>
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
      <Label htmlFor={id} required={field.required}>
        {field.label}
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
                className="text-accent-text hover:underline"
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
    acknowledged: "In Progress",
    in_progress: "In Progress",
    resolved: "Done",
  },
  servicenow: {
    open: "1",
    acknowledged: "2",
    in_progress: "2",
    resolved: "6",
  },
};

// OpsMender lifecycle statuses synced onto the external ticket, in order.
const TICKET_SYNC_STATUSES = [
  "open",
  "acknowledged",
  "in_progress",
  "resolved",
] as const;

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
  const [statusMap, setStatusMap] = useState<Record<string, string>>(
    Object.fromEntries(
      TICKET_SYNC_STATUSES.map((status) => [
        status,
        String(configuredMap[status] ?? defaults[status] ?? ""),
      ]),
    ),
  );
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
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {TICKET_SYNC_STATUSES.map((status) => (
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
            ? "Jira must send X-Hub-Signature (HMAC-SHA256) using the webhook_secret stored in the Credentials section."
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
  const formRef = useRef<HTMLElement | null>(null);
  const [editing, setEditing] = useState<IntegrationConnectorResponse | null>(
    null,
  );
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("custom");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState<IntegrationAuthType>("pat");
  const [credentialValues, setCredentialValues] = useState<
    Record<string, string>
  >({});
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [additionalVariables, setAdditionalVariables] = useState<
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

  function revealForm() {
    setFormOpen(true);
    const scroll = () => {
      formRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(scroll);
    } else {
      window.setTimeout(scroll, 0);
    }
  }

  function applyKindSelection(
    next: string,
    connector: IntegrationConnectorResponse | null = editing,
  ) {
    setKind(next);
    const definition = kinds.find((item) => item.kind === next);
    const nextAuthType = defaultAuthTypeForKind(definition);
    setAuthType(nextAuthType);
    setCredentialValues({});
    const nextCredentialFields =
      definition?.credential_fields?.[nextAuthType] ?? [];
    setConfigValues(
      valuesForFields(definition?.config_fields ?? [], connector?.config ?? {}),
    );
    setAdditionalVariables(
      mergedAdditionalRows(
        connector,
        nextCredentialFields,
        definition?.config_fields ?? [],
      ),
    );
    setRemovedCredentialKeys([]);
  }

  function startCreate(next: string) {
    setEditing(null);
    setName("");
    setBaseUrl("");
    setEnabled(true);
    setNotice("");
    applyKindSelection(next, null);
    revealForm();
  }

  function resetForm() {
    setEditing(null);
    setName("");
    setKind("custom");
    setBaseUrl("");
    setAuthType("pat");
    setCredentialValues({});
    setConfigValues({});
    setAdditionalVariables([]);
    setRemovedCredentialKeys([]);
    setEnabled(true);
    setFormOpen(false);
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
    setAdditionalVariables(
      mergedAdditionalRows(connector, fields, definition?.config_fields ?? []),
    );
    setRemovedCredentialKeys([]);
    setEnabled(connector.is_enabled);
    setNotice(
      connector.has_auth
        ? "Credentials are stored. Leave saved credential fields blank to keep them."
        : "",
    );
    revealForm();
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
      for (const key of removedCredentialKeys) auth[key] = null;

      const config: Record<string, unknown> = {};
      for (const field of configFields) {
        const value = parseFieldValue(field, fieldValue(configValues, field));
        if (value === undefined) delete config[field.name];
        else config[field.name] = value;
      }

      // One merged list of extra keys: each row is routed to the encrypted
      // credential bag (Secret) or the plaintext config bag. Structured-field
      // names and same-bag duplicate keys are rejected.
      const credentialKeys = new Set(credentialFields.map((field) => field.name));
      const configKeys = new Set(configFields.map((field) => field.name));
      const seenSecretKeys = new Set<string>();
      const seenConfigKeys = new Set<string>();
      for (const row of additionalVariables) {
        const key = row.key.trim();
        if (!key && !row.value.trim()) continue;
        if (!key) throw new Error("Every additional variable needs a key.");
        if (row.secret) {
          if (credentialKeys.has(key)) {
            throw new Error(`'${key}' is already a credential field.`);
          }
          if (seenSecretKeys.has(key)) {
            throw new Error(`Secret variable '${key}' is duplicated.`);
          }
          seenSecretKeys.add(key);
          const value = parseAdditionalValue(row);
          if (value !== undefined) auth[key] = value;
          else if (!row.saved) {
            throw new Error(`Secret variable '${key}' needs a value.`);
          }
        } else {
          if (configKeys.has(key)) {
            throw new Error(`'${key}' is already a configuration field.`);
          }
          if (seenConfigKeys.has(key)) {
            throw new Error(`Variable '${key}' is duplicated.`);
          }
          seenConfigKeys.add(key);
          const value = parseAdditionalValue(row);
          if (value === undefined) {
            throw new Error(`Variable '${key}' needs a value.`);
          }
          config[key] = value;
        }
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
                    ? ` · checked ${formatDateTime(connector.last_checked_at)}`
                    : ""}
                </p>
                {connector.last_error && (
                  <p className="mt-1 text-xs text-status-critical">
                    {connector.last_error}
                  </p>
                )}
              </div>
              <div className="flex flex-nowrap gap-2">
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
                  variant="ghost"
                  className="text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
                  aria-label={`Delete integration connector ${connector.name}`}
                  title={`Delete integration connector ${connector.name}`}
                  onClick={async () => {
                    if (!window.confirm(`Delete ${connector.name}?`)) return;
                    await deleteIntegrationConnector(connector.id);
                    await reload();
                  }}
                >
                  <Trash2 size={13} />
                </Button>
              </div>
            </div>
            {connector.kind === "jira" || connector.kind === "servicenow" ? (
              <TicketSyncPanel connector={connector} onSaved={reload} />
            ) : null}
          </article>
        ))}
      </section>

      <section className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-fg-primary">Integration catalog</h2>
            <p className="mt-1 text-sm text-fg-secondary">
              Choose a connector kind to configure.
            </p>
          </div>
          <span className="text-xs text-fg-muted">
            {kinds.length} kind{kinds.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {kinds.map((item) => (
            <button
              key={item.kind}
              type="button"
              aria-label={`Configure ${item.label}`}
              onClick={() => startCreate(item.kind)}
              className="flex min-h-20 items-start gap-3 rounded-lg border border-border-subtle bg-bg-elevated p-3 text-left transition-colors hover:border-border-strong hover:bg-bg-hover focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-bg-panel">
                {integrationKindIcon(item.kind, 20)}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-fg-primary">
                  {item.label}
                </span>
                <span className="mt-1 block text-xs text-fg-muted">
                  {item.adapter_available ? "Available" : "Config only"}
                </span>
              </span>
            </button>
          ))}
        </div>
      </section>

      {formOpen && (
      <section
        ref={formRef}
        className="space-y-4 rounded-xl border border-border-subtle bg-bg-panel p-5"
      >
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
            <Label htmlFor="integration-name" required>Name</Label>
            <Input
              id="integration-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="integration-kind">Kind</Label>
            <IconSelect
              id="integration-kind"
              value={kind}
              onChange={(next) => applyKindSelection(next)}
              options={kinds.map((item) => ({
                value: item.kind,
                label: item.label,
                icon: integrationKindIcon(item.kind),
              }))}
            />
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
                placeholder={
                  selectedKind.base_url_placeholder ??
                  "https://service.example.com"
                }
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
              {selectedKind.base_url_helper && (
                <p className="mt-1 text-xs text-fg-muted">
                  {selectedKind.base_url_helper}
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
                // The credential schema is auth-specific, so rebuild the secret
                // rows from the saved connector; preserve any config rows.
                setAdditionalVariables((rows) => [
                  ...additionalCredentialRows(
                    editing,
                    selectedKind?.credential_fields?.[nextAuthType] ?? [],
                  ),
                  ...rows.filter((row) => !row.secret),
                ]);
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
          </fieldset>
        </div>
        <AdditionalVariables
          rows={additionalVariables}
          onAdd={() =>
            setAdditionalVariables((rows) => [...rows, additionalVariable()])
          }
          onChange={(id, patch) =>
            setAdditionalVariables((rows) =>
              rows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
            )
          }
          onRemove={(target) => {
            setAdditionalVariables((rows) =>
              rows.filter((row) => row.id !== target.id),
            );
            // A removed *saved* secret key must be explicitly nulled so the
            // credential patch drops it (plaintext config rows just vanish).
            if (target.saved && target.secret) {
              setRemovedCredentialKeys((keys) => [
                ...new Set([...keys, target.key]),
              ]);
            }
          }}
        />
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
      )}
    </div>
  );
}
