"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Plus, RefreshCw, Search, X } from "lucide-react";
import {
  bulkIncidentAction,
  createIncident,
  createSession,
  getConfig,
  listIncidents,
  listServices,
  listTeams,
} from "@/lib/api";
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
  TeamResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
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

function buildIncidentColumns(
  serviceTeamName: Map<string, string>,
  teamNames: string[],
): DataTableColumn<IncidentResponse>[] {
  return [
    {
      id: "title",
      label: "Incident",
      accessor: (inc) => inc.title,
      cell: (inc) => (
        <div>
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
        </div>
      ),
      sortable: true,
      searchable: true,
    },
    {
      id: "team",
      label: "Team",
      accessor: (inc) =>
        (inc.service_id && serviceTeamName.get(inc.service_id)) || "",
      cell: (inc) => {
        const name = inc.service_id && serviceTeamName.get(inc.service_id);
        return name ? (
          <span className="text-sm text-fg-primary">{name}</span>
        ) : (
          <span className="text-fg-muted">—</span>
        );
      },
      sortable: true,
      searchable: true,
      filterChips: {
        options: teamNames.map((n) => ({ value: n, label: n })),
        valueOf: (inc) =>
          (inc.service_id && serviceTeamName.get(inc.service_id)) || null,
      },
    },
    {
      id: "source",
      label: "Source",
      accessor: (inc) => sourceMeta(inc).key,
      cell: (inc) => {
        const source = sourceMeta(inc);
        return (
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
        );
      },
      sortable: true,
      searchable: true,
      filterChips: {
        options: [
          { value: "manual", label: "Manual" },
          { value: "ingested", label: "Ingested" },
        ],
        valueOf: (inc) => sourceMeta(inc).key,
      },
    },
    {
      id: "status",
      label: "Status",
      accessor: (inc) => inc.status,
      cell: (inc) => (
        <Badge variant={inc.status as Parameters<typeof Badge>[0]["variant"]}>
          {inc.status.replace("_", " ")}
        </Badge>
      ),
      sortable: true,
      filterChips: {
        options: [
          { value: "open", label: "Open" },
          { value: "in_progress", label: "In progress" },
          { value: "resolved", label: "Resolved" },
          { value: "closed", label: "Closed" },
        ],
        valueOf: (inc) => inc.status,
      },
    },
    {
      id: "severity",
      label: "Severity",
      accessor: (inc) => inc.severity ?? "",
      cell: (inc) =>
        inc.severity ? (
          <Badge variant={inc.severity}>{inc.severity}</Badge>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
      sortable: true,
      filterChips: {
        options: [
          { value: "critical", label: "Critical" },
          { value: "high", label: "High" },
          { value: "medium", label: "Medium" },
          { value: "low", label: "Low" },
        ],
        valueOf: (inc) => inc.severity,
      },
    },
    {
      id: "updated_at",
      label: "Last activity",
      accessor: (inc) => inc.updated_at,
      cell: (inc) => (
        <div className="whitespace-nowrap">
          <p className="text-sm text-fg-primary">{fmtRelative(inc.updated_at)}</p>
          <p className="mt-0.5 text-[11px] text-fg-muted">{fmtDate(inc.updated_at)}</p>
        </div>
      ),
      sortable: true,
    },
  ];
}

function IncidentPhoneCard({
  incident,
  teamName,
}: {
  incident: IncidentResponse;
  teamName: string | null;
}) {
  const source = sourceMeta(incident);
  return (
    <div className="space-y-3">
      <div className="min-w-0">
        <Link
          href={`/dashboard/incidents/detail?id=${incident.id}`}
          className="font-medium text-fg-primary hover:text-accent"
        >
          {incident.title}
        </Link>
        <p className="mt-1 text-sm text-fg-muted">{incident.description}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
          {incident.status.replace("_", " ")}
        </Badge>
        {incident.severity ? (
          <Badge variant={incident.severity}>{incident.severity}</Badge>
        ) : null}
        {teamName ? <Badge>{teamName}</Badge> : null}
      </div>
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Source
          </p>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`inline-flex h-6 min-w-6 items-center justify-center rounded-md border px-1 text-[10px] font-semibold uppercase tracking-wide ${source.className}`}
            >
              {source.icon}
            </span>
            <span className="text-fg-primary">{source.label}</span>
          </div>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Last activity
          </p>
          <p className="mt-1 text-fg-primary">{fmtRelative(incident.updated_at)}</p>
          <p className="text-xs text-fg-muted">{fmtDate(incident.updated_at)}</p>
        </div>
      </div>
      <p className="font-mono text-[11px] text-fg-muted">
        {incident.id.slice(0, 8)}… • created {fmtDate(incident.created_at)}
      </p>
    </div>
  );
}

export default function IncidentsPage() {
  const [data, setData] = useState<IncidentListResponse | null>(null);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const toast = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();

  // Sprint 61 (Sprint E) — command-palette deep-links.
  // /dashboard/incidents?new=1 opens the create-incident modal on
  // arrival; ?test=1 opens the fire-test modal. The param is
  // consumed once and stripped from the URL so a refresh doesn't
  // re-open the modal.
  useEffect(() => {
    const wantNew = searchParams.get("new");
    const wantTest = searchParams.get("test");
    if (wantNew === "1") setShowCreate(true);
    if (wantTest === "1") setShowTest(true);
    if (wantNew === "1" || wantTest === "1") {
      const next = new URLSearchParams(searchParams.toString());
      next.delete("new");
      next.delete("test");
      const qs = next.toString();
      router.replace(`/dashboard/incidents${qs ? `?${qs}` : ""}`);
    }
  }, [searchParams, router]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Fetch a generous page; DataTable handles the rest client-side.
      const [inc, svc, tms] = await Promise.all([
        listIncidents({ limit: 200 }),
        listServices().catch(() => ({ items: [], total: 0 })),
        listTeams().catch(() => ({ items: [], total: 0 })),
      ]);
      setData(inc);
      setServices(svc.items);
      setTeams(tms.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const items = data?.items ?? [];

  const serviceTeamName = useMemo(() => {
    const teamById = new Map(teams.map((t) => [t.id, t.name]));
    const m = new Map<string, string>();
    for (const svc of services) {
      const name = teamById.get(svc.team_id);
      if (name) m.set(svc.id, name);
    }
    return m;
  }, [services, teams]);

  const teamNamesInData = useMemo(() => {
    const names = new Set<string>();
    for (const inc of items) {
      const name = inc.service_id && serviceTeamName.get(inc.service_id);
      if (name) names.add(name);
    }
    return Array.from(names).sort();
  }, [items, serviceTeamName]);

  const columns = useMemo(
    () => buildIncidentColumns(serviceTeamName, teamNamesInData),
    [serviceTeamName, teamNamesInData],
  );

  const runBulk = useCallback(
    async (
      action: "acknowledge" | "resolve" | "reassign",
      userId?: string,
    ) => {
      if (selectedIds.size === 0) return;
      setBulkBusy(true);
      try {
        const ids = Array.from(selectedIds);
        const res = await bulkIncidentAction(action, ids, userId);
        if (res.failed > 0) {
          toast.error(
            `${res.action}: ${res.succeeded} ok, ${res.failed} failed`,
          );
        } else {
          toast.success(`${res.action}: ${res.succeeded} updated`);
        }
        setSelectedIds(new Set());
        await load();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setBulkBusy(false);
      }
    },
    [selectedIds, toast, load],
  );

  const overview = useMemo(() => {
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
    ] as const;
  }, [items]);

  return (
    <div className="mx-auto max-w-6xl">
      <SetupChecklist />
      <div className="mb-6">
        <PageHeader
          title="Incidents"
          subtitle={data ? `${data.total} incidents` : undefined}
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

      {/* Table */}
      {loading && !data ? (
        <TableSkeleton rows={6} columns={5} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title="No incidents yet"
          description="Incidents you create or receive from integrations will appear here."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus size={14} />
              New Incident
            </Button>
          }
        />
      ) : (
        <DataTable
          rows={items}
          columns={columns}
          rowKey={(inc) => inc.id}
          phoneLayout={(inc) => (
            <IncidentPhoneCard
              incident={inc}
              teamName={(inc.service_id && serviceTeamName.get(inc.service_id)) || null}
            />
          )}
          storageKey="opsmender:incidents-table"
          searchPlaceholder="Search by title, description, team, or source…"
          selectable
          selectedKeys={selectedIds}
          onSelectionChange={setSelectedIds}
          dateRangeColumn={{
            id: "updated_at",
            label: "Last activity",
            valueOf: (inc) => inc.updated_at,
          }}
          bulkActions={() => (
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={bulkBusy}
                onClick={() => runBulk("acknowledge")}
              >
                Acknowledge
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={bulkBusy}
                onClick={() => runBulk("resolve")}
              >
                Resolve
              </Button>
            </>
          )}
        />
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
