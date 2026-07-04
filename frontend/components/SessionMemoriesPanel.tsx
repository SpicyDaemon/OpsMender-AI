"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Brain, ChevronDown, ChevronRight, ThumbsDown, ThumbsUp } from "lucide-react";

import {
  getSessionMemoriesUsed,
  recordMemoryFeedback,
} from "@/lib/api";
import type {
  IncidentMemoryResponse,
  SessionMemoriesUsedItem,
} from "@/lib/types";
import { formatDateTime } from "@/lib/formatDate";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { useToast } from "@/components/ui/Toast";

interface Props {
  sessionId: string;
  defaultOpen?: boolean;
}

export function SessionMemoriesPanel({ sessionId, defaultOpen = false }: Props) {
  const toast = useToast();
  const { user } = useAuth();
  const canVote = user?.role === "admin" || user?.role === "operator";

  const [open, setOpen] = useState(defaultOpen);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<SessionMemoriesUsedItem[]>([]);
  const [fetched, setFetched] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getSessionMemoriesUsed(sessionId);
      setItems(resp.items);
      setFetched(true);
    } catch (err) {
      // Don't toast on every mount — session memory recall may legitimately
      // be empty for a fresh org. Only log.
      console.warn("memories-used fetch failed", err);
      setFetched(true);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    // Lazy: only fetch when the operator expands the section.
    if (open && !fetched) {
      load();
    }
  }, [open, fetched, load]);

  const vote = async (memory: IncidentMemoryResponse, helpful: boolean) => {
    if (!canVote) return;
    try {
      const updated = await recordMemoryFeedback(memory.id, helpful);
      setItems((prev) =>
        prev.map((it) =>
          it.memory.id === updated.id ? { ...it, memory: updated } : it,
        ),
      );
      toast.success(helpful ? "Thanks — marked helpful" : "Thanks — marked not helpful");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-bg-hover/40"
      >
        <div className="flex items-center gap-2">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Brain size={14} className="text-accent-text" />
          <span className="text-xs font-medium uppercase tracking-wide text-fg-secondary">
            Memories used
          </span>
          {fetched && (
            <Badge variant="default">{items.length}</Badge>
          )}
        </div>
        {fetched && items.length > 0 && (
          <Link
            href="/dashboard/memories"
            className="text-xs text-fg-muted hover:text-fg-primary"
            onClick={(e) => e.stopPropagation()}
          >
            Manage memories →
          </Link>
        )}
      </button>

      {open && (
        <div className="border-t border-border-subtle px-4 py-3">
          {loading && !fetched ? (
            <p className="text-sm text-fg-muted">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-fg-muted">
              No memories surfaced for this session. The agent had nothing
              relevant to recall from this service yet — successful sessions
              build memory automatically.
            </p>
          ) : (
            <ul className="space-y-3">
              {items.map((item) => (
                <li
                  key={item.memory.id}
                  className="rounded-md border border-border-subtle bg-bg-elevated p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h4 className="text-sm font-semibold text-fg-primary">
                        {item.memory.title}
                      </h4>
                      <p className="mt-0.5 text-[11px] text-fg-muted">
                        Surfaced {formatDateTime(item.surfaced_at)}
                        {item.score != null &&
                          ` · relevance ${item.score.toFixed(2)}`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => vote(item.memory, true)}
                        disabled={!canVote}
                        title="Helpful"
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-secondary hover:bg-bg-hover hover:text-fg-primary disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <ThumbsUp size={12} /> {item.memory.helpful_count}
                      </button>
                      <button
                        type="button"
                        onClick={() => vote(item.memory, false)}
                        disabled={!canVote}
                        title="Not helpful"
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-secondary hover:bg-bg-hover hover:text-fg-primary disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <ThumbsDown size={12} /> {item.memory.unhelpful_count}
                      </button>
                    </div>
                  </div>
                  {item.memory.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {item.memory.tags.map((t) => (
                        <Badge key={t} variant="default">
                          {t}
                        </Badge>
                      ))}
                    </div>
                  )}
                  <p className="mt-2 whitespace-pre-wrap text-xs text-fg-secondary">
                    {item.memory.summary_md}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
