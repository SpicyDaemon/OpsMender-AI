"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Plus, RefreshCw, Search, X } from "lucide-react";
import { createIncident, listIncidents } from "@/lib/api";
import type {
  IncidentCreate,
  IncidentListResponse,
  IncidentResponse,
  IncidentStatus,
  Severity,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
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
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listIncidents({ status: statusFilter || undefined, limit: 100 });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, toast]);

  useEffect(() => { load(); }, [load]);

  const filteredItems = useMemo(() => {
    const items = data?.items ?? [];
    const normalizedQuery = query.trim().toLowerCase();

    return items.filter((incident) => {
      const source = sourceMeta(incident).key;
      const matchesSeverity = !severityFilter || incident.severity === severityFilter;
      const matchesSource = !sourceFilter || source === sourceFilter;
      const matchesQuery =
        !normalizedQuery
        || incident.title.toLowerCase().includes(normalizedQuery)
        || incident.description.toLowerCase().includes(normalizedQuery)
        || incident.id.toLowerCase().includes(normalizedQuery)
        || (incident.external_source ?? "").toLowerCase().includes(normalizedQuery);

      return matchesSeverity && matchesSource && matchesQuery;
    });
  }, [data?.items, query, severityFilter, sourceFilter]);

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
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg-primary">Incidents</h1>
          {data && (
            <p className="mt-0.5 text-sm text-fg-secondary">
              Showing {filteredItems.length} of {data.total} incidents
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus size={14} />
            New Incident
          </Button>
        </div>
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
      <div className="mb-4 rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,0.8fr))_auto]">
          <div>
            <Label htmlFor="inc-search">Search</Label>
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />
              <Input
                id="inc-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search title, description, ID, or source…"
                className="pl-9"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="inc-status">Status</Label>
            <Select
              id="inc-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as IncidentStatus | "")}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <Label htmlFor="inc-severity">Severity</Label>
            <Select
              id="inc-severity"
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value as Severity | "")}
            >
              {SEVERITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <Label htmlFor="inc-source">Source</Label>
            <Select
              id="inc-source"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value as "" | "manual" | "ingested")}
            >
              {SOURCE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>

          <div className="flex items-end">
            <Button
              variant="secondary"
              size="sm"
              className="w-full justify-center"
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
