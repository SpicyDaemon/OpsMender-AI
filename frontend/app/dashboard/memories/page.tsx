"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Brain,
  Check,
  EyeOff,
  Pencil,
  Plus,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react";

import {
  createMemory,
  deleteMemory,
  listMemories,
  listServices,
  recordMemoryFeedback,
  reviewMemory,
  setMemoryHidden,
  updateMemory,
} from "@/lib/api";
import type {
  IncidentMemoryResponse,
  ServiceResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const GLOBAL_SERVICE = "__global";

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function MemoriesPage() {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canEdit = isAdmin || user?.role === "operator";

  const [memories, setMemories] = useState<IncidentMemoryResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [includeHidden, setIncludeHidden] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<IncidentMemoryResponse | null>(null);
  const [deleting, setDeleting] = useState<IncidentMemoryResponse | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const params: { include_hidden?: boolean } = {};
      if (includeHidden) params.include_hidden = true;
      const [memResp, svcResp] = await Promise.all([
        listMemories(params),
        listServices(),
      ]);
      setMemories(memResp.items);
      setServices(svcResp.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [includeHidden, toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const serviceNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of services) map.set(s.id, s.name);
    return map;
  }, [services]);

  const serviceFilterOptions = useMemo(
    () => [
      { value: GLOBAL_SERVICE, label: "Global" },
      ...services.map((s) => ({ value: s.id, label: s.name })),
    ],
    [services],
  );

  const expandedKeys = useMemo(
    () => (expanded ? new Set([expanded]) : new Set<string>()),
    [expanded],
  );

  const handleFeedback = useCallback(
    async (memory: IncidentMemoryResponse, helpful: boolean) => {
      try {
        const updated = await recordMemoryFeedback(memory.id, helpful);
        setMemories((prev) =>
          prev.map((m) => (m.id === updated.id ? updated : m)),
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [toast],
  );

  const handleReview = useCallback(
    async (
      memory: IncidentMemoryResponse,
      status: "approved" | "rejected",
    ) => {
      try {
        const updated = await reviewMemory(memory.id, status);
        setMemories((prev) =>
          prev.map((m) => (m.id === updated.id ? updated : m)),
        );
        toast.success(
          status === "approved"
            ? `Approved "${updated.title}" — it can now be recalled by the AI.`
            : `Rejected "${updated.title}" — it will not be recalled.`,
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    },
    [toast],
  );

  const pendingCount = useMemo(
    () => memories.filter((m) => m.review_status === "pending").length,
    [memories],
  );

  const columns = useMemo<DataTableColumn<IncidentMemoryResponse>[]>(
    () => [
      {
        id: "title",
        label: "Memory",
        accessor: (memory) => `${memory.title} ${memory.summary_md}`,
        cell: (memory) => (
          <div className="min-w-[16rem]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-fg-primary">
                {memory.title}
              </span>
              {memory.review_status === "pending" && (
                <Badge variant="in_progress">Pending review</Badge>
              )}
              {memory.review_status === "rejected" && (
                <Badge variant="default">Rejected</Badge>
              )}
              {memory.is_hidden && <Badge variant="default">Hidden</Badge>}
            </div>
            <p className="mt-1 line-clamp-2 max-w-xl text-xs text-fg-muted">
              {memory.summary_md}
            </p>
          </div>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "service",
        label: "Service",
        accessor: (memory) =>
          memory.service_id
            ? serviceNameById.get(memory.service_id) ?? memory.service_id
            : "Global",
        cell: (memory) => (
          <span className="text-sm text-fg-secondary">
            {memory.service_id
              ? serviceNameById.get(memory.service_id) ?? memory.service_id
              : "Global"}
          </span>
        ),
        sortable: true,
        filterChips: {
          options: serviceFilterOptions,
          valueOf: (memory) => memory.service_id ?? GLOBAL_SERVICE,
        },
      },
      {
        id: "tags",
        label: "Tags",
        accessor: (memory) => memory.tags.join(" "),
        cell: (memory) =>
          memory.tags.length > 0 ? (
            <div className="flex max-w-sm flex-wrap gap-1">
              {memory.tags.map((tag) => (
                <Badge key={tag} variant="default">
                  {tag}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
        searchable: true,
      },
      {
        id: "feedback",
        label: "Feedback",
        accessor: (memory) => memory.helpful_count - memory.unhelpful_count,
        cell: (memory) => (
          <div className="flex items-center gap-1">
            <button
              type="button"
              title="Helpful"
              onClick={() => canEdit && handleFeedback(memory, true)}
              disabled={!canEdit}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-secondary hover:bg-bg-hover hover:text-fg-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ThumbsUp size={13} /> {memory.helpful_count}
            </button>
            <button
              type="button"
              title="Not helpful"
              onClick={() => canEdit && handleFeedback(memory, false)}
              disabled={!canEdit}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-secondary hover:bg-bg-hover hover:text-fg-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ThumbsDown size={13} /> {memory.unhelpful_count}
            </button>
          </div>
        ),
        sortable: true,
      },
      {
        id: "last_used",
        label: "Last surfaced",
        accessor: (memory) => memory.last_used_at ?? "",
        cell: (memory) => (
          <span className="whitespace-nowrap text-xs text-fg-secondary">
            {fmtDate(memory.last_used_at)}
          </span>
        ),
        sortable: true,
        hiddenByDefault: true,
      },
      {
        id: "review",
        label: "Review",
        accessor: (memory) => memory.review_status,
        cell: (memory) => (
          <Badge
            variant={
              memory.review_status === "approved"
                ? "resolved"
                : memory.review_status === "pending"
                  ? "in_progress"
                  : "default"
            }
          >
            {memory.review_status === "approved"
              ? "Approved"
              : memory.review_status === "pending"
                ? "Pending"
                : "Rejected"}
          </Badge>
        ),
        filterChips: {
          options: [
            { value: "pending", label: "Pending" },
            { value: "approved", label: "Approved" },
            { value: "rejected", label: "Rejected" },
          ],
          valueOf: (memory) => memory.review_status,
        },
        sortable: true,
      },
      {
        id: "visibility",
        label: "Visibility",
        accessor: (memory) => (memory.is_hidden ? "hidden" : "visible"),
        cell: (memory) => (
          <Badge variant={memory.is_hidden ? "default" : "resolved"}>
            {memory.is_hidden ? "Hidden" : "Visible"}
          </Badge>
        ),
        filterChips: includeHidden
          ? {
              options: [
                { value: "visible", label: "Visible" },
                { value: "hidden", label: "Hidden" },
              ],
              valueOf: (memory) =>
                memory.is_hidden ? "hidden" : "visible",
            }
          : undefined,
        hiddenByDefault: true,
      },
    ],
    [
      canEdit,
      handleFeedback,
      handleReview,
      includeHidden,
      serviceFilterOptions,
      serviceNameById,
    ],
  );

  const handleHide = async (memory: IncidentMemoryResponse) => {
    try {
      const updated = await setMemoryHidden(memory.id, !memory.is_hidden);
      if (!includeHidden && updated.is_hidden) {
        setMemories((prev) => prev.filter((m) => m.id !== updated.id));
      } else {
        setMemories((prev) =>
          prev.map((m) => (m.id === updated.id ? updated : m)),
        );
      }
      toast.success(updated.is_hidden ? "Memory hidden" : "Memory shown");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    try {
      await deleteMemory(deleting.id);
      setMemories((prev) => prev.filter((m) => m.id !== deleting.id));
      toast.success("Memory deleted");
      setDeleting(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Memories"
        subtitle="Per-service lessons the agent has learned from prior incidents. AI-written memories need review before they are recalled into sessions; operator-authored ones are approved on save."
        icon={<Brain size={18} />}
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            Refresh
          </Button>
        }
      />

      {canEdit && pendingCount > 0 && (
        <div className="flex items-center gap-2 rounded-md border border-status-medium-border bg-status-medium-bg px-3 py-2 text-sm text-status-medium">
          <Brain size={14} />
          <span>
            {pendingCount} AI-written{" "}
            {pendingCount === 1 ? "memory is" : "memories are"} awaiting review.
            Approve to let the AI recall {pendingCount === 1 ? "it" : "them"};
            reject to keep but never recall. Filter by{" "}
            <span className="font-medium">Review → Pending</span>.
          </span>
        </div>
      )}

      {loading && memories.length === 0 ? (
        <TableSkeleton rows={4} columns={4} />
      ) : memories.length === 0 ? (
        <EmptyState
          icon={Brain}
          title="No memories yet"
          description="Memories accumulate automatically after successfully resolved sessions. You can also author one by hand."
          learnMoreHref="/docs/wiki/memory-guide.md"
          learnMoreLabel="Memory Guide"
          action={
            canEdit ? (
              <Button onClick={() => setCreateOpen(true)}>
                <Plus size={14} /> New memory
              </Button>
            ) : undefined
          }
        />
      ) : (
        <DataTable
          rows={memories}
          columns={columns}
          rowKey={(memory) => memory.id}
          storageKey="opsmender:memories-table"
          filterBar
          searchPlaceholder="Search title, summary, tag, or service…"
          toolbarRight={
            <div className="flex items-center gap-3">
              {isAdmin && (
                <label className="flex items-center gap-2 text-xs text-fg-secondary">
                  <input
                    type="checkbox"
                    checked={includeHidden}
                    onChange={(e) => setIncludeHidden(e.target.checked)}
                  />
                  Include hidden
                </label>
              )}
              {canEdit && (
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  <Plus size={14} /> New memory
                </Button>
              )}
            </div>
          }
          expandedRow={{
            expandedKeys,
            onToggle: (key) =>
              setExpanded((cur) => (cur === key ? null : key)),
            render: (memory) => (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                  Summary
                </p>
                <p className="whitespace-pre-wrap text-sm text-fg-secondary">
                  {memory.summary_md}
                </p>
              </div>
            ),
            label: "Memory summary",
          }}
          rowActions={(memory) => (
            <div className="flex justify-end gap-1">
              {canEdit && memory.review_status !== "approved" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleReview(memory, "approved")}
                  title="Approve — allow the AI to recall this memory"
                >
                  <Check size={13} />
                </Button>
              )}
              {canEdit && memory.review_status !== "rejected" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleReview(memory, "rejected")}
                  title="Reject — keep but never recall into AI sessions"
                >
                  <X size={13} />
                </Button>
              )}
              {canEdit && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(memory)}
                  title="Edit"
                >
                  <Pencil size={13} />
                </Button>
              )}
              {isAdmin && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleHide(memory)}
                    title={memory.is_hidden ? "Unhide" : "Hide"}
                  >
                    <EyeOff size={13} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleting(memory)}
                    title="Delete"
                  >
                    <Trash2 size={13} />
                  </Button>
                </>
              )}
            </div>
          )}
        />
      )}

      {createOpen && (
        <MemoryEditor
          mode="create"
          services={services}
          onClose={() => setCreateOpen(false)}
          onSubmit={async (payload) => {
            try {
              const created = await createMemory(payload);
              setMemories((prev) => [created, ...prev]);
              toast.success("Memory created");
              setCreateOpen(false);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : String(err));
            }
          }}
        />
      )}
      {editing && (
        <MemoryEditor
          mode="edit"
          memory={editing}
          services={services}
          onClose={() => setEditing(null)}
          onSubmit={async (payload) => {
            try {
              const updated = await updateMemory(editing.id, {
                ...payload,
                service_id_set: true,
              });
              setMemories((prev) =>
                prev.map((m) => (m.id === updated.id ? updated : m)),
              );
              toast.success("Memory updated");
              setEditing(null);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : String(err));
            }
          }}
        />
      )}
      {deleting && (
        <Modal
          open={true}
          onClose={() => setDeleting(null)}
          title="Delete memory?"
        >
          <p className="text-sm text-fg-secondary">
            This permanently removes &ldquo;
            <span className="font-medium text-fg-primary">{deleting.title}</span>
            &rdquo;. Future sessions on this service will no longer see this
            lesson. Consider hiding instead — it preserves the audit trail.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={handleDelete}>
              Delete
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function MemoryEditor({
  mode,
  memory,
  services,
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  memory?: IncidentMemoryResponse;
  services: ServiceResponse[];
  onClose: () => void;
  onSubmit: (payload: {
    title: string;
    summary_md: string;
    tags: string[];
    service_id: string | null;
  }) => Promise<void>;
}) {
  const [title, setTitle] = useState(memory?.title ?? "");
  const [summary, setSummary] = useState(memory?.summary_md ?? "");
  const [tagsText, setTagsText] = useState((memory?.tags ?? []).join(", "));
  const [serviceId, setServiceId] = useState(memory?.service_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!title.trim()) {
      setError("Title is required");
      return;
    }
    if (!summary.trim()) {
      setError("Summary is required");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const tags = tagsText
        .split(",")
        .map((t) => t.trim().toLowerCase())
        .filter(Boolean)
        .slice(0, 5);
      await onSubmit({
        title: title.trim(),
        summary_md: summary,
        tags,
        service_id: serviceId || null,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={mode === "create" ? "Create memory" : "Edit memory"}
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="mem-title">Title</Label>
          <Input
            id="mem-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            placeholder="Short headline, e.g. Pod OOMKilled = memory limit too low"
          />
        </div>
        <div>
          <Label htmlFor="mem-summary">Summary (markdown)</Label>
          <Textarea
            id="mem-summary"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            rows={8}
            maxLength={4000}
            placeholder="Symptoms · Cause · Fix · Gotchas"
          />
        </div>
        <div>
          <Label htmlFor="mem-tags">Tags (comma-separated, max 5)</Label>
          <Input
            id="mem-tags"
            value={tagsText}
            onChange={(e) => setTagsText(e.target.value)}
            placeholder="k8s, oom, high"
          />
        </div>
        <div>
          <Label htmlFor="mem-service">Service</Label>
          <Select
            id="mem-service"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
          >
            <option value="">(global — applies to any service)</option>
            {services.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {mode === "create" ? "Create" : "Save"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
