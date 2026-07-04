"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Brain,
  ChevronDown,
  Pencil,
  Plus,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";

import {
  bulkDeleteMemories,
  createMemory,
  deleteMemory,
  listMemories,
  listServices,
  recordMemoryFeedback,
  updateMemory,
} from "@/lib/api";
import type {
  IncidentMemoryResponse,
  ServiceResponse,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
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
import { formatDate } from "@/lib/formatDate";

const GLOBAL_SERVICE = "__global";

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return formatDate(iso);
}

// AI-authored summaries arrive as loose markdown with occasional LaTeX
// artifacts (e.g. "**What went wrong:**", "$\rightarrow$"). We render them as
// clean text rather than leaking the raw markers into the table.
function normalizeMarkup(md: string): string {
  return md
    .replace(/\$\s*\\?rightarrow\s*\$/gi, "→")
    .replace(/\\rightarrow/gi, "→")
    .replace(/\$([^$\n]*)\$/g, "$1") // strip stray $…$ math delimiters
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "");
}

/** Single-line preview: normalized markup with whitespace collapsed. */
function summaryPreview(md: string): string {
  return normalizeMarkup(md)
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** "data-integrity" → "Data integrity" — display-only; stored value unchanged. */
function tagLabel(tag: string): string {
  const t = tag.replace(/[-_]/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : tag;
}

const MAX_VISIBLE_TAGS = 3;

export default function MemoriesPage() {
  const toast = useToast();
  const { user } = useAuth();
  const canEdit = user?.role === "admin" || user?.role === "operator";

  const [memories, setMemories] = useState<IncidentMemoryResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [actionsOpen, setActionsOpen] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<IncidentMemoryResponse | null>(null);
  const [deleting, setDeleting] = useState<IncidentMemoryResponse[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [memResp, svcResp] = await Promise.all([
        listMemories(),
        listServices(),
      ]);
      setMemories(memResp.items);
      setSelectedKeys((current) => {
        const valid = new Set(memResp.items.map((memory) => memory.id));
        return new Set([...current].filter((id) => valid.has(id)));
      });
      setServices(svcResp.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [toast]);

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
            </div>
            <p className="mt-1 line-clamp-2 max-w-xl text-xs text-fg-muted">
              {summaryPreview(memory.summary_md)}
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
              {memory.tags.slice(0, MAX_VISIBLE_TAGS).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center whitespace-nowrap rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-secondary"
                >
                  {tagLabel(tag)}
                </span>
              ))}
              {memory.tags.length > MAX_VISIBLE_TAGS && (
                <span
                  className="inline-flex items-center rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-muted"
                  title={memory.tags.slice(MAX_VISIBLE_TAGS).map(tagLabel).join(", ")}
                >
                  +{memory.tags.length - MAX_VISIBLE_TAGS}
                </span>
              )}
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
    ],
    [
      canEdit,
      handleFeedback,
      serviceFilterOptions,
      serviceNameById,
    ],
  );

  const handleDelete = async () => {
    if (deleting.length === 0) return;
    try {
      const ids = deleting.map((memory) => memory.id);
      if (ids.length === 1) {
        await deleteMemory(ids[0]);
      } else {
        await bulkDeleteMemories(ids);
      }
      const deletedIds = new Set(ids);
      setMemories((prev) => prev.filter((m) => !deletedIds.has(m.id)));
      setSelectedKeys(new Set());
      toast.success(
        ids.length === 1 ? "Memory deleted" : `${ids.length} memories deleted`,
      );
      setDeleting([]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const selectedMemories = useMemo(
    () => memories.filter((memory) => selectedKeys.has(memory.id)),
    [memories, selectedKeys],
  );
  const selectionManageable =
    selectedMemories.length > 0 &&
    selectedMemories.every((memory) => memory.can_edit && memory.can_delete);
  const selectionBlockedReason =
    selectedMemories.length > 0 && !selectionManageable
      ? "Selection includes global memories or memories owned by another team."
      : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Memories"
        subtitle="Per-service lessons the agent continuously learns from resolved incidents and recalls automatically in future sessions."
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
          selectable={canEdit}
          selectedKeys={selectedKeys}
          onSelectionChange={setSelectedKeys}
          storageKey="opsmender:memories-table"
          filterBar
          searchPlaceholder="Search title, summary, tag, or service…"
          toolbarRight={
            <div className="flex items-center gap-2">
              {canEdit && (
                <Button
                  className="h-11"
                  onClick={() => setCreateOpen(true)}
                >
                  <Plus size={14} /> New memory
                </Button>
              )}
              {canEdit && (
                <div className="relative">
                  <Button
                    data-testid="memory-actions-trigger"
                    className="h-11"
                    variant={selectedKeys.size > 0 ? "primary" : "secondary"}
                    disabled={selectedKeys.size === 0}
                    onClick={() => setActionsOpen((open) => !open)}
                    title={selectionBlockedReason}
                  >
                    Actions <ChevronDown size={13} />
                  </Button>
                  {actionsOpen && selectedKeys.size > 0 && (
                    <>
                      <button
                        type="button"
                        className="fixed inset-0 z-10 cursor-default"
                        aria-label="Close memory actions"
                        onClick={() => setActionsOpen(false)}
                      />
                      <div className="absolute right-0 top-full z-20 mt-1 w-48 rounded-md border border-border-default bg-bg-panel p-1 shadow-lg">
                        <button
                          data-testid="memory-action-edit"
                          type="button"
                          disabled={
                            selectedMemories.length !== 1 ||
                            !selectedMemories[0]?.can_edit
                          }
                          onClick={() => {
                            setEditing(selectedMemories[0]);
                            setActionsOpen(false);
                          }}
                          className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Pencil size={14} /> Edit
                        </button>
                        <button
                          data-testid="memory-action-delete"
                          type="button"
                          disabled={!selectionManageable}
                          title={selectionBlockedReason}
                          onClick={() => {
                            setDeleting(selectedMemories);
                            setActionsOpen(false);
                          }}
                          className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-status-critical hover:bg-status-critical-bg disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Trash2 size={14} />{" "}
                          {selectedMemories.length === 1
                            ? "Delete"
                            : "Delete all"}
                        </button>
                      </div>
                    </>
                  )}
                </div>
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
                  {normalizeMarkup(memory.summary_md)}
                </p>
              </div>
            ),
            label: "Memory summary",
          }}
          rowActions={(memory) => (
            <div className="flex justify-end gap-1">
              {memory.can_edit && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(memory)}
                  title="Edit"
                >
                  <Pencil size={13} />
                </Button>
              )}
              {memory.can_delete && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setDeleting([memory])}
                  title="Delete"
                >
                  <Trash2 size={13} />
                </Button>
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
      {deleting.length > 0 && (
        <Modal
          open={true}
          onClose={() => setDeleting([])}
          title={deleting.length === 1 ? "Delete memory?" : "Delete memories?"}
        >
          <p className="text-sm text-fg-secondary">
            {deleting.length === 1 ? (
              <>
                This permanently removes &ldquo;
                <span className="font-medium text-fg-primary">
                  {deleting[0].title}
                </span>
                &rdquo;. Future sessions will no longer recall this lesson.
              </>
            ) : (
              <>
                Are you sure you want to delete {deleting.length} memories?
                This cannot be undone.
              </>
            )}
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeleting([])}>
              Cancel
            </Button>
            <Button
              data-testid="confirm-memory-delete"
              variant="danger"
              onClick={handleDelete}
            >
              {deleting.length === 1 ? "Delete" : "Delete all"}
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
