"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Brain,
  EyeOff,
  Pencil,
  Plus,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";

import {
  createMemory,
  deleteMemory,
  listMemories,
  listServices,
  recordMemoryFeedback,
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
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterChips } from "@/components/ui/FilterChips";
import { FormError, Input, Label, Select, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

type ServiceFilter = "" | "global" | string;

export default function MemoriesPage() {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const canEdit = isAdmin || user?.role === "operator";

  const [memories, setMemories] = useState<IncidentMemoryResponse[]>([]);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [serviceFilter, setServiceFilter] = useState<ServiceFilter>("");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [search, setSearch] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<IncidentMemoryResponse | null>(null);
  const [deleting, setDeleting] = useState<IncidentMemoryResponse | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const params: { service_id?: string; include_hidden?: boolean } = {};
      if (serviceFilter && serviceFilter !== "global") {
        params.service_id = serviceFilter;
      }
      if (includeHidden) params.include_hidden = true;
      const [memResp, svcResp] = await Promise.all([
        listMemories(params),
        listServices(),
      ]);
      let items = memResp.items;
      if (serviceFilter === "global") {
        items = items.filter((m) => !m.service_id);
      }
      setMemories(items);
      setServices(svcResp.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [serviceFilter, includeHidden, toast]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filteredMemories = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return memories;
    return memories.filter(
      (m) =>
        m.title.toLowerCase().includes(q) ||
        m.summary_md.toLowerCase().includes(q) ||
        m.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [memories, search]);

  const serviceNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of services) map.set(s.id, s.name);
    return map;
  }, [services]);

  const serviceOptions = useMemo(
    () => [
      { value: "" as ServiceFilter, label: "All" },
      { value: "global" as ServiceFilter, label: "Global" },
      ...services.map((s) => ({ value: s.id as ServiceFilter, label: s.name })),
    ],
    [services],
  );

  const handleFeedback = async (memory: IncidentMemoryResponse, helpful: boolean) => {
    try {
      const updated = await recordMemoryFeedback(memory.id, helpful);
      setMemories((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

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
        subtitle="Per-service lessons the agent has learned from prior incidents. Curate them like SKILL.md."
        icon={<Brain size={18} />}
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={refresh}
              disabled={loading}
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Refresh
            </Button>
            {canEdit && (
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus size={14} /> New memory
              </Button>
            )}
          </>
        }
      />

      <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-panel p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <Label htmlFor="mem-search">Search</Label>
            <Input
              id="mem-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title, summary, or tag…"
            />
          </div>
          {isAdmin && (
            <label className="flex items-center gap-2 text-xs text-fg-secondary pb-2">
              <input
                type="checkbox"
                checked={includeHidden}
                onChange={(e) => setIncludeHidden(e.target.checked)}
              />
              Include hidden
            </label>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-[6rem_1fr] sm:items-center">
          <span className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            Service
          </span>
          <FilterChips
            ariaLabel="Filter by service"
            options={serviceOptions}
            value={serviceFilter}
            onChange={(v) => setServiceFilter(v)}
          />
        </div>
      </div>

      {loading && memories.length === 0 ? (
        <TableSkeleton rows={4} columns={4} />
      ) : filteredMemories.length === 0 ? (
        <EmptyState
          icon={Brain}
          title={
            search
              ? "No memories match this search"
              : "No memories yet"
          }
          description={
            search
              ? "Clear the search to see all memories for this service."
              : "Memories accumulate automatically after successfully resolved sessions. You can also author one by hand."
          }
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
        <ul className="space-y-3">
          {filteredMemories.map((memory) => (
            <li
              key={memory.id}
              className={`rounded-lg border ${
                memory.is_hidden
                  ? "border-border-subtle bg-bg-surface opacity-60"
                  : "border-border-default bg-bg-panel"
              } p-4 shadow-sm`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-base font-semibold text-fg-primary">
                      {memory.title}
                    </h3>
                    {memory.is_hidden && (
                      <Badge variant="default">Hidden</Badge>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    {memory.service_id
                      ? `Service: ${
                          serviceNameById.get(memory.service_id) ??
                          memory.service_id
                        }`
                      : "Global memory"}
                    {memory.last_used_at &&
                      ` · last surfaced ${new Date(
                        memory.last_used_at,
                      ).toLocaleDateString()}`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
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
              </div>
              {memory.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {memory.tags.map((t) => (
                    <Badge key={t} variant="default">
                      {t}
                    </Badge>
                  ))}
                </div>
              )}
              <p className="mt-3 whitespace-pre-wrap text-sm text-fg-secondary">
                {memory.summary_md}
              </p>
            </li>
          ))}
        </ul>
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
