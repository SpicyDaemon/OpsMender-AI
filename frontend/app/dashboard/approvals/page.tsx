"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  Clock,
  CornerUpRight,
  RefreshCw,
  Shield,
  Sparkles,
  XCircle,
} from "lucide-react";
import {
  approveRequest,
  extendApprovalRequest,
  listApprovals,
  rejectRequest,
  redirectRequest,
} from "@/lib/api";
import type { ApprovalListResponse, ApprovalRequestResponse, ApprovalStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useLiveEvents } from "@/context/liveEvents";
import { autonomyTierDisplay, titleCaseIdentifier } from "@/lib/displayNames";
import { formatDateTime } from "@/lib/formatDate";

function fmtDate(iso: string) {
  return formatDateTime(iso);
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

function approvalActionLabel(action: Record<string, unknown>) {
  const tool = action.tool ?? action.tool_name ?? action.name ?? action.action;
  if (typeof tool === "string" && tool.trim()) return titleCaseIdentifier(tool);
  return "Requested action";
}

function approvalActionDetail(action: Record<string, unknown>) {
  const target = action.target ?? action.resource ?? action.service ?? action.kind;
  if (typeof target === "string" && target.trim()) return titleCaseIdentifier(target);
  const keys = Object.keys(action).filter((key) => !["tool", "tool_name", "name", "action"].includes(key));
  return keys.length > 0 ? `${keys.length} parameter${keys.length === 1 ? "" : "s"}` : "Review required";
}

function approvalEmptyStateCopy(statusFilter: ApprovalStatus | "") {
  if (statusFilter === "pending") {
    return {
      title: "No approvals waiting",
      description:
        "Tier 1 actions that need human sign-off will appear here as soon as a session asks for approval.",
      showTestAction: false,
    };
  }

  if (!statusFilter) {
    return {
      title: "No approvals yet",
      description:
        "Tier 1 actions that need human sign-off show up here. You can also approve them directly from the session detail page or your chat — this is the catch-up inbox.",
      showTestAction: true,
    };
  }

  return {
    title: `No ${titleCaseIdentifier(statusFilter).toLowerCase()} approvals`,
    description: "No approvals match this status. Try another filter or switch back to Pending for work waiting on you.",
    showTestAction: false,
  };
}

function ApprovalTierBadge({ tier }: { tier: number | null }) {
  if (tier === null) return null;
  const tierInfo = autonomyTierDisplay(tier);
  return (
    <Badge className={`border ${tierInfo.className}`}>
      <span className="font-mono">T{tier}</span>
      <span aria-hidden="true"> · </span>
      {tierInfo.label}
    </Badge>
  );
}

function ActionContextDisclosure({
  id,
  action,
  open,
  onToggle,
}: {
  id: string;
  action: Record<string, unknown>;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="mt-3">
      <button
        type="button"
        className="flex w-full items-center justify-between rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-left text-xs font-medium text-fg-secondary sm:hidden"
        aria-expanded={open}
        aria-controls={`approval-action-context-${id}`}
        onClick={onToggle}
      >
        {open ? "Hide action details" : "Show action details"}
        <ChevronDown
          size={14}
          className={`transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      <pre
        id={`approval-action-context-${id}`}
        className={`mt-2 text-xs bg-bg-elevated rounded-lg border border-border-subtle p-4 overflow-x-auto font-mono leading-relaxed ${
          open ? "block" : "hidden"
        } sm:block`}
      >
        {JSON.stringify(action, null, 2)}
      </pre>
    </div>
  );
}

export default function ApprovalsPage() {
  const [data, setData] = useState<ApprovalListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "">("pending");
  const [selected, setSelected] = useState<ApprovalRequestResponse | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionDetailsOpen, setActionDetailsOpen] = useState<Record<string, boolean>>({});
  const [redirectDrafts, setRedirectDrafts] = useState<Record<string, string>>({});
  const toast = useToast();

  const load = useCallback(async ({ background = false } = {}) => {
    if (background) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const res = await listApprovals({ status: statusFilter || undefined, limit: 100 });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      if (background) {
        setRefreshing(false);
      } else {
        setLoading(false);
      }
    }
  }, [statusFilter, toast]);

  useEffect(() => { void load(); }, [load]);

  useLiveEvents(["approval"], () => {
    void load({ background: true });
  });

  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") void load({ background: true });
    };
    const interval = window.setInterval(refreshIfVisible, 30_000);
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [load]);

  async function handleApprove(id: string) {
    setActionLoading(true);
    try {
      await approveRequest(id);
      toast.success("Approval granted");
      setSelected(null);
      void load({ background: true });
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
      void load({ background: true });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Rejection failed");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleRedirect(id: string) {
    const guidance = (redirectDrafts[id] ?? "").trim();
    if (!guidance) return;
    setActionLoading(true);
    try {
      await redirectRequest(id, guidance);
      toast.info("Approval redirected");
      setSelected(null);
      setRedirectDrafts((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      void load({ background: true });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Redirect failed");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleExtend(id: string) {
    setActionLoading(true);
    try {
      const updated = await extendApprovalRequest(id);
      toast.success("Session hold extended");
      setSelected(updated);
      void load({ background: true });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Extension failed");
    } finally {
      setActionLoading(false);
    }
  }

  const pendingCount = data?.items.filter((a) => a.status === "pending").length ?? 0;
  const emptyStateCopy = approvalEmptyStateCopy(statusFilter);

  return (
    <div>
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
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void load()}
              disabled={loading || refreshing}
            >
              <RefreshCw
                size={14}
                className={loading || refreshing ? "animate-spin" : ""}
              />
              Refresh
            </Button>
          }
        />
      </div>

      {/* Filter bar */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <SegmentedControl
          ariaLabel="Filter by approval status"
          options={STATUS_OPTIONS.map((o) => ({
            ...o,
            count: o.value
              ? (data?.status_counts?.[o.value] ?? 0)
              : undefined,
          }))}
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
          title={emptyStateCopy.title}
          description={emptyStateCopy.description}
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            emptyStateCopy.showTestAction ? (
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
        <div className="space-y-3">
          <div className="space-y-3 sm:hidden">
            {data?.items.map((a) => {
              const isPending = a.status === "pending";
              const detailId = `card-${a.id}`;
              const redirectGuidance = redirectDrafts[a.id] ?? "";
              return (
                <article
                  key={a.id}
                  className={`rounded-xl border p-4 shadow-sm ${
                    isPending
                      ? "border-status-medium-border bg-status-medium-bg/20"
                      : "border-border-subtle bg-bg-panel"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => setSelected(a)}
                      className="min-w-0 text-left"
                    >
                      <span className="block text-sm font-semibold text-fg-primary">
                        {approvalActionLabel(a.action)}
                      </span>
                      <span className="mt-0.5 block text-xs text-fg-muted">
                        {approvalActionDetail(a.action)}
                      </span>
                    </button>
                    <Badge variant={a.status}>{titleCaseIdentifier(a.status)}</Badge>
                  </div>
                  <div className="mt-2">
                    <ApprovalTierBadge tier={a.session_tier} />
                  </div>

                  {a.justification && (
                    <p className="mt-3 text-xs text-fg-secondary">
                      <span className="font-medium text-fg-muted">Reason:</span>{" "}
                      {a.justification}
                    </p>
                  )}

                  <div className="mt-3 grid gap-1.5 text-xs text-fg-muted">
                    <Link
                      href={`/dashboard/sessions/detail?id=${a.session_id}`}
                      className="font-medium text-accent-text hover:underline"
                    >
                      Open session{" "}
                      <span className="font-mono text-fg-muted">
                        {a.session_id.slice(0, 8)}
                      </span>
                    </Link>
                    <p className="tabular-nums">Requested {fmtDate(a.requested_at)}</p>
                    <p className="tabular-nums">
                      {isPending ? `Expires in ${timeUntil(a.expires_at)}` : "Expired"}
                    </p>
                  </div>

                  <ActionContextDisclosure
                    id={detailId}
                    action={a.action}
                    open={actionDetailsOpen[detailId] === true}
                    onToggle={() =>
                      setActionDetailsOpen((prev) => ({
                        ...prev,
                        [detailId]: !prev[detailId],
                      }))
                    }
                  />

                  {isPending && (
                    <>
                      <div className="mt-3">
                        <label
                          htmlFor={`redirect-guidance-${a.id}`}
                          className="text-[11px] font-medium text-fg-muted"
                        >
                          Redirect guidance
                        </label>
                        <textarea
                          id={`redirect-guidance-${a.id}`}
                          value={redirectGuidance}
                          onChange={(e) =>
                            setRedirectDrafts((prev) => ({
                              ...prev,
                              [a.id]: e.target.value,
                            }))
                          }
                          placeholder="e.g. gather logs first, then retry the action"
                          rows={2}
                          className="mt-1 w-full resize-none rounded-lg border border-border-subtle bg-bg-input px-3 py-2 text-xs shadow-sm placeholder:text-fg-muted focus:border-accent focus:ring-1 focus:ring-accent transition-colors"
                        />
                      </div>
                      <div className="mt-3 flex flex-col gap-2">
                        <Button
                          size="sm"
                          variant="success"
                          onClick={() => handleApprove(a.id)}
                          loading={actionLoading}
                          className="h-11 w-full"
                        >
                          <CheckCircle2 size={14} />
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleReject(a.id)}
                          loading={actionLoading}
                          className="h-11 w-full text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
                        >
                          <XCircle size={14} />
                          Reject
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => handleRedirect(a.id)}
                          loading={actionLoading}
                          disabled={!redirectGuidance.trim()}
                          className="h-11 w-full"
                        >
                          <CornerUpRight size={14} />
                          Redirect
                        </Button>
                      </div>
                    </>
                  )}
                </article>
              );
            })}
          </div>

          <div className="hidden overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm sm:block">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Session</th>
                <th className="px-4 py-3">Tier</th>
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
                        className="text-left"
                      >
                        <span className="block text-sm font-medium text-fg-primary hover:text-accent-text">
                          {approvalActionLabel(a.action)}
                        </span>
                        <span className="mt-0.5 block text-xs text-fg-muted">
                          {approvalActionDetail(a.action)}
                        </span>
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
                        className="text-xs text-fg-secondary hover:text-accent-text transition-colors"
                      >
                        Open session
                        <span className="ml-1 font-mono text-fg-muted">
                          {a.session_id.slice(0, 8)}
                        </span>
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <ApprovalTierBadge tier={a.session_tier} />
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={a.status}>{titleCaseIdentifier(a.status)}</Badge>
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
                              variant="ghost"
                              onClick={() => handleReject(a.id)}
                              loading={actionLoading}
                              className="min-w-[80px] text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
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
              <ActionContextDisclosure
                id={`modal-${selected.id}`}
                action={selected.action}
                open={actionDetailsOpen[`modal-${selected.id}`] === true}
                onToggle={() =>
                  setActionDetailsOpen((prev) => ({
                    ...prev,
                    [`modal-${selected.id}`]: !prev[`modal-${selected.id}`],
                  }))
                }
              />
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
                  className="font-mono text-xs text-accent-text hover:underline"
                >
                  {selected.session_id}
                </Link>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Status</p>
                <Badge variant={selected.status}>{titleCaseIdentifier(selected.status)}</Badge>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Tier</p>
                <ApprovalTierBadge tier={selected.session_tier} />
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Requested</p>
                <p className="text-xs text-fg-primary tabular-nums font-mono">{formatDateTime(selected.requested_at)}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Expires</p>
                <p className="text-xs text-fg-primary tabular-nums font-mono">
                  {formatDateTime(selected.expires_at)}
                  {selected.status === "pending" && (
                    <span className="ml-2 text-status-medium">({timeUntil(selected.expires_at)})</span>
                  )}
                </p>
              </div>
            </div>

            {selected.status === "pending" && (
              <div className="flex flex-col gap-2 pt-2 border-t border-border-subtle sm:flex-row sm:justify-end sm:gap-3">
                <Button
                  variant="success"
                  onClick={() => handleApprove(selected.id)}
                  loading={actionLoading}
                  className="h-11 w-full sm:h-auto sm:w-auto sm:min-w-[110px]"
                >
                  <CheckCircle2 size={16} />
                  Approve
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => handleReject(selected.id)}
                  loading={actionLoading}
                  className="h-11 w-full text-status-critical hover:bg-status-critical-bg hover:text-status-critical sm:h-auto sm:w-auto sm:min-w-[110px]"
                >
                  <XCircle size={16} />
                  Reject
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => handleExtend(selected.id)}
                  loading={actionLoading}
                  className="h-11 w-full sm:h-auto sm:w-auto"
                >
                  <Clock size={16} />
                  Extend session
                </Button>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
