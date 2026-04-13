"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Plus, RefreshCw } from "lucide-react";
import { createIncident, listIncidents } from "@/lib/api";
import type { IncidentCreate, IncidentListResponse, IncidentResponse, Severity } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageSpinner } from "@/components/ui/Spinner";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_OPTIONS = ["", "open", "in_progress", "resolved", "closed"];

export default function IncidentsPage() {
  const [data, setData] = useState<IncidentListResponse | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listIncidents({ status: statusFilter || undefined, limit: 50 });
      setData(res);
    } catch {
      // silently fail — keep stale data
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Incidents</h1>
          {data && (
            <p className="text-sm text-gray-500 mt-0.5">{data.total} total</p>
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

      {/* Filter */}
      <div className="mb-4">
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-48"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s ? s.replace("_", " ") : "All statuses"}</option>
          ))}
        </Select>
      </div>

      {/* Table */}
      {loading && !data ? (
        <PageSpinner />
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-3">Title</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data?.items.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                    No incidents found.
                  </td>
                </tr>
              )}
              {data?.items.map((inc) => (
                <tr key={inc.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/incidents/${inc.id}`}
                      className="font-medium text-gray-900 hover:text-indigo-600"
                    >
                      {inc.title}
                    </Link>
                    <p className="text-xs text-gray-400 truncate max-w-xs mt-0.5">
                      {inc.description}
                    </p>
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
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                    {fmtDate(inc.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create modal */}
      <CreateIncidentModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(inc) => {
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
