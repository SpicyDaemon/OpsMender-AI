"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  CheckSquare,
  Clock,
  RefreshCw,
  Shield,
  Sparkles,
  XCircle,
} from "lucide-react";
import { approveRequest, listApprovals, rejectRequest } from "@/lib/api";
import type { ApprovalListResponse, ApprovalRequestResponse, ApprovalStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChips } from "@/components/ui/FilterChips";
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

function timeUntil(iso: string): string {
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return "expired";
  const mins = Math.ceil(diff / 60000);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

const STATUS_OPTIONS: { value: ApprovalStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
];

export default function ApprovalsPage() {
  const [data, setData] = useState<ApprovalListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "">("pending");
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
      <div className="mb-6">
        <PageHeader
          title={
            <span className="flex items-center gap-2">
              Approvals
              {pendingCount > 0 && (
                <span className="inline-flex items-center justify-center rounded-full bg-status-medium text-fg-inverse text-xs font-bold min-w-[22px] h-[22px] px-1.5 animate-pulse">
                  {pendingCount}
                </span>
              )}
            </span>
          }
          subtitle={data ? `${data.total} total requests` : undefined}
          icon={<Shield size={18} />}
          actions={
            <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Refresh
            </Button>
          }
        />
      </div>

      {/* Filter bar */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <FilterChips
          ariaLabel="Filter by approval status"
          options={STATUS_OPTIONS}
          value={statusFilter}
          onChange={(v) => setStatusFilter(v)}
        />
        <span className="text-xs text-fg-muted">
          {data ? `${data.items.length} shown` : ""}
        </span>
      </div>

      {/* Pending urgency banner */}
      {pendingCount > 0 && statusFilter !== "pending" && (
        <button
          onClick={() => setStatusFilter("pending")}
          className="mb-4 w-full flex items-center gap-2 rounded-lg border border-status-medium-border bg-status-medium-bg/50 px-4 py-2.5 text-sm text-status-medium font-medium hover:bg-status-medium-bg/70 transition-colors"
        >
          <Clock size={14} />
          {pendingCount} approval{pendingCount > 1 ? "s" : ""} waiting for review — click to view
        </button>
      )}

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
              : "Tier 1 actions that need human sign-off show up here. You can also approve them directly from the session detail page or your chat — this is the catch-up inbox."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            !statusFilter ? (
              <Link
                href="/dashboard/incidents?test=1"
                className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-panel px-2.5 py-1 text-xs font-medium text-fg-primary transition-colors hover:bg-bg-hover"
              >
                <Sparkles size={14} />
                Fire test incident
              </Link>
            ) : undefined
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
                <th className="px-4 py-3">Time left</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {data?.items.map((a) => {
                const isPending = a.status === "pending";
                return (
                  <tr
                    key={a.id}
                    className={`transition-colors ${
                      isPending
                        ? "bg-status-medium-bg/20 hover:bg-status-medium-bg/30"
                        : "hover:bg-bg-elevated"
                    }`}
                  >
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setSelected(a)}
                        className="font-mono text-xs text-accent hover:underline text-left block"
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
                        className="font-mono text-xs text-fg-secondary hover:text-accent transition-colors"
                      >
                        {a.session_id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={a.status}>{a.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-fg-secondary whitespace-nowrap text-xs tabular-nums font-mono">
                      {fmtDate(a.requested_at)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {isPending ? (
                        <span className="text-xs font-medium text-status-medium tabular-nums font-mono">
                          <Clock size={10} className="inline mr-1" />
                          {timeUntil(a.expires_at)}
                        </span>
                      ) : (
                        <span className="text-xs text-fg-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <Link
                          href={`/dashboard/sessions/detail?id=${a.session_id}`}
                          className="inline-flex items-center gap-1 rounded-md border border-border-default bg-bg-surface px-2.5 py-1 text-xs font-medium text-fg-primary hover:bg-bg-elevated transition-colors"
                          title="Open the session that requested this approval"
                        >
                          Open session →
                        </Link>
                        {isPending && (
                          <>
                            <Button
                              size="sm"
                              variant="success"
                              onClick={() => handleApprove(a.id)}
                              loading={actionLoading}
                              className="min-w-[90px]"
                            >
                              <CheckCircle2 size={14} />
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="danger"
                              onClick={() => handleReject(a.id)}
                              loading={actionLoading}
                              className="min-w-[80px]"
                            >
                              <XCircle size={14} />
                              Reject
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
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
          <div className="space-y-5">
            {/* Action preview */}
            <div>
              <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-2">
                Action context
              </p>
              <pre className="text-xs bg-bg-elevated rounded-lg border border-border-subtle p-4 overflow-x-auto font-mono leading-relaxed">
                {JSON.stringify(selected.action, null, 2)}
              </pre>
            </div>

            {selected.justification && (
              <div>
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1">Justification</p>
                <p className="text-sm text-fg-primary">{selected.justification}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4 text-sm rounded-lg border border-border-subtle bg-bg-elevated p-4">
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Session</p>
                <Link
                  href={`/dashboard/sessions/detail?id=${selected.session_id}`}
                  className="font-mono text-xs text-accent hover:underline"
                >
                  {selected.session_id}
                </Link>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Status</p>
                <Badge variant={selected.status}>{selected.status}</Badge>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Requested</p>
                <p className="text-xs text-fg-primary tabular-nums font-mono">{new Date(selected.requested_at).toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Expires</p>
                <p className="text-xs text-fg-primary tabular-nums font-mono">
                  {new Date(selected.expires_at).toLocaleString()}
                  {selected.status === "pending" && (
                    <span className="ml-2 text-status-medium">({timeUntil(selected.expires_at)})</span>
                  )}
                </p>
              </div>
            </div>

            {selected.status === "pending" && (
              <div className="flex justify-end gap-3 pt-2 border-t border-border-subtle">
                <Button
                  variant="danger"
                  onClick={() => handleReject(selected.id)}
                  loading={actionLoading}
                  className="min-w-[110px]"
                >
                  <XCircle size={16} />
                  Reject
                </Button>
                <Button
                  variant="success"
                  onClick={() => handleApprove(selected.id)}
                  loading={actionLoading}
                  className="min-w-[110px]"
                >
                  <CheckCircle2 size={16} />
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
