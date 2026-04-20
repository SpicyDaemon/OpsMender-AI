"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckSquare, RefreshCw } from "lucide-react";
import { approveRequest, listApprovals, rejectRequest } from "@/lib/api";
import type { ApprovalListResponse, ApprovalRequestResponse, ApprovalStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Input";
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

const STATUS_OPTIONS: { value: ApprovalStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
];

export default function ApprovalsPage() {
  const [data, setData] = useState<ApprovalListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "">("");
  const [selected, setSelected] = useState<ApprovalRequestResponse | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listApprovals({ status: statusFilter || undefined, limit: 100 });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, toast]);

  useEffect(() => { load(); }, [load]);

  async function handleApprove(id: string) {
    setActionLoading(true);
    try {
      await approveRequest(id);
      toast.success("Approval granted");
      setSelected(null);
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleReject(id: string) {
    setActionLoading(true);
    try {
      await rejectRequest(id);
      toast.info("Approval rejected");
      setSelected(null);
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Rejection failed");
    } finally {
      setActionLoading(false);
    }
  }

  const pendingCount = data?.items.filter((a) => a.status === "pending").length ?? 0;

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-fg-primary">
            Approvals
            {pendingCount > 0 && (
              <span className="ml-2 inline-flex items-center justify-center rounded-full bg-status-medium text-fg-primary text-xs font-bold w-5 h-5">
                {pendingCount}
              </span>
            )}
          </h1>
          {data && (
            <p className="text-sm text-fg-secondary mt-0.5">{data.total} total</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>

      {/* Filter */}
      <div className="mb-4">
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ApprovalStatus | "")}
          className="w-44"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </Select>
      </div>

      {/* Table */}
      {loading && !data ? (
        <TableSkeleton rows={6} columns={6} />
      ) : data?.items.length === 0 ? (
        <EmptyState
          icon={CheckSquare}
          title={statusFilter ? `No ${statusFilter} approvals` : "No approvals yet"}
          description={
            statusFilter
              ? "Try a different status filter."
              : "Tier 1 actions that need human sign-off will show up here."
          }
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Session</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Requested</th>
                <th className="px-4 py-3">Expires</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {data?.items.map((a) => (
                <tr key={a.id} className="hover:bg-bg-elevated transition-colors">
                  <td className="px-4 py-3">
                    <button
                      onClick={() => setSelected(a)}
                      className="font-mono text-xs text-accent hover:underline text-left"
                    >
                      {JSON.stringify(a.action).slice(0, 60)}…
                    </button>
                    {a.justification && (
                      <p className="text-xs text-fg-muted mt-0.5 truncate max-w-xs">
                        {a.justification}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/sessions/detail?id=${a.session_id}`}
                      className="font-mono text-xs text-fg-secondary hover:text-accent"
                    >
                      {a.session_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={a.status}>{a.status}</Badge>
                  </td>
                  <td className="px-4 py-3 text-fg-secondary whitespace-nowrap text-xs">
                    {fmtDate(a.requested_at)}
                  </td>
                  <td className="px-4 py-3 text-fg-secondary whitespace-nowrap text-xs">
                    {fmtDate(a.expires_at)}
                  </td>
                  <td className="px-4 py-3">
                    {a.status === "pending" && (
                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          variant="success"
                          onClick={() => handleApprove(a.id)}
                          loading={actionLoading}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => handleReject(a.id)}
                          loading={actionLoading}
                        >
                          Reject
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail modal */}
      {selected && (
        <Modal
          open={!!selected}
          onClose={() => setSelected(null)}
          title="Approval Request"
          maxWidth="max-w-2xl"
        >
          <div className="space-y-4">
            <div>
              <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1.5">Action</p>
              <pre className="text-xs bg-bg-elevated rounded-lg border border-border-subtle p-4 overflow-x-auto">
                {JSON.stringify(selected.action, null, 2)}
              </pre>
            </div>

            {selected.justification && (
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Justification</p>
                <p className="text-sm text-fg-primary">{selected.justification}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Session</p>
                <Link
                  href={`/dashboard/sessions/detail?id=${selected.session_id}`}
                  className="font-mono text-xs text-accent hover:underline"
                >
                  {selected.session_id}
                </Link>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Status</p>
                <Badge variant={selected.status}>{selected.status}</Badge>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Requested</p>
                <p className="text-xs text-fg-primary">{new Date(selected.requested_at).toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Expires</p>
                <p className="text-xs text-fg-primary">{new Date(selected.expires_at).toLocaleString()}</p>
              </div>
            </div>

            {selected.status === "pending" && (
              <div className="flex justify-end gap-2 pt-2 border-t border-border-subtle">
                <Button
                  variant="danger"
                  onClick={() => handleReject(selected.id)}
                  loading={actionLoading}
                >
                  Reject
                </Button>
                <Button
                  variant="success"
                  onClick={() => handleApprove(selected.id)}
                  loading={actionLoading}
                >
                  Approve
                </Button>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
