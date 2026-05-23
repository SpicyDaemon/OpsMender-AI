"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Plus, RefreshCw, Search, X } from "lucide-react";
import { createIncident, createSession, getConfig, listIncidents, listServices } from "@/lib/api";
import { SetupChecklist } from "@/components/SetupChecklist";
import type {
  ConfigResponse,
  IncidentCreate,
  IncidentListResponse,
  IncidentResponse,
  IncidentStatus,
  ServiceResponse,
  SessionResponse,
  Severity,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChips } from "@/components/ui/FilterChips";
import { Input, Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtRelative(iso: string) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return fmtDate(iso);
}

function sourceMeta(incident: IncidentResponse) {
  if (!incident.external_source) {
    return {
      key: "manual",
      label: "Manual",
      icon: "M",
      className: "bg-status-info-bg text-status-info border-status-info-border",
    };
  }
  if (incident.external_source === "opsmender-test") {
    return {
      key: "ingested",
      label: "Test",
      icon: "T",
      className: "bg-status-high-bg text-status-high border-status-high-border",
    };
  }
  const raw = incident.external_source.replace(/^auto:/, "").replace(/_/g, " ");
  const label = raw
    .split(":")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" / ");
  return {
    key: "ingested",
    label: label || "Ingested",
    icon: (label || "I").slice(0, 2).toUpperCase(),
    className: "bg-status-medium-bg text-status-medium border-status-medium-border",
  };
}

const STATUS_OPTIONS: { value: IncidentStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

const SEVERITY_OPTIONS: { value: Severity | ""; label: string }[] = [
  { value: "", label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const SOURCE_OPTIONS = [
  { value: "", label: "All sources" },
  { value: "manual", label: "Manual" },
  { value: "ingested", label: "Ingested" },
];

export default function IncidentsPage() {
  const [data, setData] = useState<IncidentListResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState<IncidentStatus | "">("");
  const [severityFilter, setSeverityFilter] = useState<Severity | "">("");
  const [sourceFilter, setSourceFilter] = useState<"" | "manual" | "ingested">("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const toast = useToast();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listIncidents({
        status: statusFilter || undefined,
        q: debouncedQuery || undefined,
        limit: 100,
      });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [debouncedQuery, statusFilter, toast]);

  useEffect(() => { load(); }, [load]);

  const filteredItems = useMemo(() => {
    const items = data?.items ?? [];

    return items.filter((incident) => {
      const source = sourceMeta(incident).key;
      const matchesSeverity = !severityFilter || incident.severity === severityFilter;
      const matchesSource = !sourceFilter || source === sourceFilter;
      return matchesSeverity && matchesSource;
    });
  }, [data?.items, severityFilter, sourceFilter]);

  const hasLocalFilters = Boolean(query || severityFilter || sourceFilter);
  const hasAnyFilters = Boolean(statusFilter || hasLocalFilters);

  const overview = useMemo(() => {
    const items = filteredItems;
    return [
      {
        label: "Visible",
        value: String(items.length),
        tone: "text-fg-primary",
      },
      {
        label: "Critical",
        value: String(items.filter((item) => item.severity === "critical").length),
        tone: "text-status-critical",
      },
      {
        label: "Open",
        value: String(items.filter((item) => item.status === "open").length),
        tone: "text-status-info",
      },
      {
        label: "Ingested",
        value: String(items.filter((item) => sourceMeta(item).key === "ingested").length),
        tone: "text-status-medium",
      },
    ];
  }, [filteredItems]);

  return (
    <div className="mx-auto max-w-6xl">
      <SetupChecklist />
      <div className="mb-6">
        <PageHeader
          title="Incidents"
          subtitle={
            data
              ? `Showing ${filteredItems.length} of ${data.total} incidents`
              : undefined
          }
          actions={
            <>
              <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                Refresh
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setShowTest(true)}>
                Fire Test Incident
              </Button>
              <Button size="sm" onClick={() => setShowCreate(true)}>
                <Plus size={14} />
                New Incident
              </Button>
            </>
          }
        />
      </div>

      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {overview.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm"
          >
            <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
              {item.label}
            </p>
            <p className={`mt-2 text-2xl font-semibold tracking-tight ${item.tone}`}>
              {item.value}
            </p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="mb-4 space-y-3 rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <Label htmlFor="inc-search">Search</Label>
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />
              <Input
                id="inc-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search title or description…"
                className="pl-9"
              />
            </div>
            {query.trim() !== debouncedQuery && (
              <p className="mt-1 text-xs text-fg-muted">Searching…</p>
            )}
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={!hasAnyFilters}
            onClick={() => {
              setQuery("");
              setStatusFilter("");
              setSeverityFilter("");
              setSourceFilter("");
            }}
          >
            <X size={14} />
            Clear
          </Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-[6rem_1fr] sm:items-center">
          <span className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            Status
          </span>
          <FilterChips
            ariaLabel="Filter by status"
            options={STATUS_OPTIONS}
            value={statusFilter}
            onChange={(v) => setStatusFilter(v)}
          />

          <span className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            Severity
          </span>
          <FilterChips
            ariaLabel="Filter by severity"
            options={SEVERITY_OPTIONS}
            value={severityFilter}
            onChange={(v) => setSeverityFilter(v)}
          />

          <span className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            Source
          </span>
          <FilterChips
            ariaLabel="Filter by source"
            options={SOURCE_OPTIONS as readonly { value: "" | "manual" | "ingested"; label: string }[]}
            value={sourceFilter}
            onChange={(v) => setSourceFilter(v)}
          />
        </div>
      </div>

      {/* Table */}
      {loading && !data ? (
        <TableSkeleton rows={6} columns={5} />
      ) : filteredItems.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title={hasAnyFilters ? "No incidents match these filters" : "No incidents yet"}
          description={
            hasAnyFilters
              ? "Try widening the filters or clearing the search to see more incidents."
              : "Incidents you create or receive from integrations will appear here."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            !hasAnyFilters && (
              <Button size="sm" onClick={() => setShowCreate(true)}>
                <Plus size={14} />
                New Incident
              </Button>
            )
          }
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
                <th className="px-4 py-3">Incident</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Last Activity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {filteredItems.map((inc) => {
                const source = sourceMeta(inc);
                return (
                <tr key={inc.id} className="hover:bg-bg-elevated transition-colors">
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/incidents/detail?id=${inc.id}`}
                      className="font-medium text-fg-primary hover:text-accent"
                    >
                      {inc.title}
                    </Link>
                    <p className="mt-0.5 max-w-md truncate text-xs text-fg-muted">
                      {inc.description}
                    </p>
                    <p className="mt-1 font-mono text-[11px] text-fg-muted">
                      {inc.id.slice(0, 8)}… • created {fmtDate(inc.created_at)}
                    </p>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex h-7 min-w-7 items-center justify-center rounded-md border px-1 text-[10px] font-semibold uppercase tracking-wide ${source.className}`}
                      >
                        {source.icon}
                      </span>
                      <div className="min-w-0">
                        <p className="truncate text-sm text-fg-primary">{source.label}</p>
                        <p className="truncate text-[11px] text-fg-muted">
                          {inc.external_id ?? "Operator-created"}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={inc.status as Parameters<typeof Badge>[0]["variant"]}>
                      {inc.status.replace("_", " ")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    {inc.severity ? (
                      <Badge variant={inc.severity}>{inc.severity}</Badge>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <p className="text-sm text-fg-primary">{fmtRelative(inc.updated_at)}</p>
                    <p className="mt-0.5 text-[11px] text-fg-muted">{fmtDate(inc.updated_at)}</p>
                  </td>
                </tr>
              )})}
            </tbody>
          </table>
        </div>
      )}

      {/* Create modal */}
      <CreateIncidentModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => {
          setShowCreate(false);
          load();
        }}
      />
      <FireTestIncidentModal
        open={showTest}
        onClose={() => setShowTest(false)}
        onCreated={(incident, session) => {
          setShowTest(false);
          toast.success("Test incident created and session started.", {
            label: "Open session",
            href: `/dashboard/sessions/detail?id=${session.id}`,
          });
          load();
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create incident modal
// ---------------------------------------------------------------------------

function CreateIncidentModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (inc: IncidentResponse) => void;
}) {
  const [form, setForm] = useState<IncidentCreate>({
    title: "",
    description: "",
    severity: undefined,
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function reset() {
    setForm({ title: "", description: "", severity: undefined });
    setError("");
  }

  async function handleSubmit() {
    setError("");
    setLoading(true);
    try {
      const inc = await createIncident(form);
      reset();
      onCreated(inc);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { reset(); onClose(); }}
      title="Create Incident"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="ci-title">Title</Label>
          <Input
            id="ci-title"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Database cluster unreachable"
          />
        </div>
        <div>
          <Label htmlFor="ci-desc">Description</Label>
          <Textarea
            id="ci-desc"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Describe the incident in detail..."
            rows={4}
          />
        </div>
        <div>
          <Label htmlFor="ci-sev">Severity</Label>
          <Select
            id="ci-sev"
            value={form.severity ?? ""}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                severity: (e.target.value as Severity) || undefined,
              }))
            }
          >
            <option value="">None</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </Select>
        </div>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={() => { reset(); onClose(); }}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={loading} disabled={!form.title || !form.description}>
            Create
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function createSyntheticPayload(serviceName?: string): Pick<
  IncidentCreate,
  "title" | "description" | "severity" | "external_id" | "external_source"
> {
  const scope = serviceName ? ` for ${serviceName}` : "";
  return {
    title: `TEST · synthetic alert${scope}`,
    description:
      `Synthetic alert fired from the Incidents page${scope}. ` +
      "Use this to verify ingestion, paging, sessions, and operator workflow end to end.",
    severity: "high",
    external_id: `test-${Date.now()}`,
    external_source: "opsmender-test",
  };
}

function FireTestIncidentModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (incident: IncidentResponse, session: SessionResponse) => void;
}) {
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [serviceId, setServiceId] = useState("");
  const [form, setForm] = useState<IncidentCreate>(() => createSyntheticPayload());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    listServices()
      .then((res) => setServices(res.items))
      .catch(() => setServices([]));
    getConfig()
      .then((res) => setConfig(res))
      .catch(() => setConfig(null));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const selectedService = services.find((service) => service.id === serviceId);
    const base = createSyntheticPayload(selectedService?.name);
    setForm({
      ...base,
      service_id: serviceId || undefined,
    });
  }, [open, serviceId, services]);

  function reset() {
    setServiceId("");
    setForm(createSyntheticPayload());
    setError("");
  }

  async function handleSubmit() {
    setError("");
    setLoading(true);
    try {
      const incident = await createIncident(form);
      const session = await createSession({
        incident_id: incident.id,
        tier: config?.tier ?? 2,
        initial_briefing:
          "TEST · synthetic alert. Validate the response path without treating this as a real production incident.",
      });
      reset();
      onCreated(incident, session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fire test incident");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Fire Test Incident"
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-status-high-border bg-status-high-bg/40 px-4 py-3 text-sm text-fg-primary">
          This creates a synthetic high-severity incident and immediately starts a session so you can verify the operator flow end to end.
        </div>

        <div>
          <Label htmlFor="test-service">Service (optional)</Label>
          <Select
            id="test-service"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
          >
            <option value="">No linked service</option>
            {services.map((service) => (
              <option key={service.id} value={service.id}>
                {service.name}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="test-title">Synthetic Payload</Label>
          <Textarea
            id="test-title"
            rows={6}
            value={JSON.stringify(
              {
                title: form.title,
                description: form.description,
                severity: form.severity,
                source: form.external_source,
                service_id: form.service_id ?? null,
              },
              null,
              2,
            )}
            readOnly
            className="font-mono text-xs"
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="secondary"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={loading}>
            Fire Test Incident
          </Button>
        </div>
      </div>
    </Modal>
  );
}
