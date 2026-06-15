"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Activity,
  Bell,
  ChevronRight,
  CircleDot,
  Clock3,
  MessageSquare,
  Play,
  Send,
  Shield,
  Siren,
  Terminal,
} from "lucide-react";
import { createIncidentComment } from "@/lib/api";
import type { IncidentTimelineItemResponse } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Textarea } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";

function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timelineAccent(item: IncidentTimelineItemResponse) {
  if (item.lane === "tool") return "border-accent/50 bg-accent/5";
  if (item.lane === "evidence") return "border-status-medium-border bg-status-medium-bg/30";
  if (item.lane === "comment") return "border-accent/40 bg-accent/5";
  if (item.lane === "notification") return "border-border-subtle bg-bg-elevated";
  if (item.status === "blocked" || item.status === "error") {
    return "border-status-critical-border bg-status-critical-bg/25";
  }
  return "border-border-subtle bg-bg-elevated";
}

function timelineIcon(item: IncidentTimelineItemResponse) {
  if (item.lane === "tool") return <Terminal size={14} className="text-accent" />;
  if (item.lane === "evidence") return <Activity size={14} className="text-status-medium" />;
  if (item.lane === "comment")
    return <MessageSquare size={14} className="text-accent" />;
  if (item.lane === "notification")
    return <Bell size={14} className="text-fg-secondary" />;
  if (item.event_type === "escalation_step_fired") {
    return <Siren size={14} className="text-status-high" />;
  }
  return <CircleDot size={14} className="text-fg-secondary" />;
}

function prettyJson(value: Record<string, unknown> | null) {
  if (!value) return "";
  return JSON.stringify(value, null, 2);
}

export function IncidentTimeline({
  items,
  error,
  activeSessionId,
  onSelectSession,
  onStartSession,
  incidentId,
  canComment = false,
  onCommentAdded,
}: {
  items: IncidentTimelineItemResponse[];
  error: string;
  activeSessionId: string;
  onSelectSession: (sessionId: string) => void;
  onStartSession: () => void;
  incidentId?: string;
  canComment?: boolean;
  onCommentAdded?: () => void | Promise<void>;
}) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [posting, setPosting] = useState(false);

  async function handlePostComment() {
    if (!incidentId || !comment.trim()) return;
    setPosting(true);
    try {
      await createIncidentComment(incidentId, comment.trim());
      setComment("");
      await onCommentAdded?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not post comment");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 sm:px-5 sm:py-4">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Incident Timeline
          </p>
          <h2 className="mt-1 text-base font-semibold text-fg-primary sm:text-lg">
            Response activity, AI actions, and alert evidence
          </h2>
        </div>
        <Button size="sm" variant="secondary" onClick={onStartSession}>
          <Play size={14} />
          <span className="hidden sm:inline">New Session</span>
          <span className="sm:hidden">New</span>
        </Button>
      </div>

      {canComment && incidentId && (
        <div className="border-b border-border-subtle px-4 py-3 sm:px-5">
          <Textarea
            aria-label="Add a comment"
            rows={2}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Add a comment to the incident timeline…"
          />
          <div className="mt-2 flex justify-end">
            <Button
              size="sm"
              onClick={handlePostComment}
              loading={posting}
              disabled={!comment.trim()}
            >
              <Send size={13} /> Comment
            </Button>
          </div>
        </div>
      )}

      <div className="p-3 sm:p-5">
        {error ? (
          <div className="rounded-lg border border-status-high-border bg-status-high-bg px-4 py-4 text-sm text-fg-secondary">
            We couldn&apos;t load the command timeline right now. You can still start a new session and use the rest of the incident surface.
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Clock3}
            title="No timeline activity yet"
            description="Once an alert lands, a page fires, or an AI session starts, the command timeline will build here."
            action={(
              <Button size="sm" onClick={onStartSession}>
                <Play size={14} />
                Start Session
              </Button>
            )}
          />
        ) : (
          <div className="space-y-3">
            {items.map((item, index) => (
              <div key={item.id} className="flex gap-3">
                <div className="flex w-7 flex-col items-center pt-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full border border-border-subtle bg-bg-panel">
                    {timelineIcon(item)}
                  </span>
                  {index < items.length - 1 ? (
                    <span className="mt-2 h-full min-h-8 w-px bg-border-subtle" />
                  ) : null}
                </div>

                <div className={`min-w-0 flex-1 rounded-lg border px-4 py-4 ${timelineAccent(item)}`}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-fg-primary">{item.title}</p>
                        {item.session_label ? (
                          <span className="rounded-md border border-border-subtle bg-bg-panel px-2 py-0.5 text-[11px] font-semibold text-fg-secondary">
                            {item.session_label}
                          </span>
                        ) : null}
                        {item.session_tier !== null ? (
                          <span className="rounded-md border border-border-subtle bg-bg-panel px-2 py-0.5 font-mono text-[11px] text-fg-secondary">
                            Tier {item.session_tier}
                          </span>
                        ) : null}
                        {item.status ? (
                          <Badge
                            variant={
                              item.status === "blocked"
                                ? "failed"
                                : item.status === "acknowledged"
                                  ? "completed"
                                  : item.status === "active"
                                    ? "active"
                                    : item.status === "resolved" || item.status === "closed"
                                      ? "completed"
                                      : "default"
                            }
                          >
                            {item.status.replaceAll("_", " ")}
                          </Badge>
                        ) : null}
                        {item.safety_class ? (
                          <span className="inline-flex items-center gap-1 rounded-md border border-border-subtle bg-bg-panel px-2 py-0.5 text-[11px] text-fg-secondary">
                            <Shield size={11} />
                            {item.safety_class}
                          </span>
                        ) : null}
                        {item.tier_decision ? (
                          <span className="rounded-md border border-border-subtle bg-bg-panel px-2 py-0.5 text-[11px] text-fg-secondary">
                            {item.tier_decision}
                          </span>
                        ) : null}
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
                        <span>{fmtDateTime(item.happened_at)}</span>
                        {item.actor_label ? <span>Actor: {item.actor_label}</span> : null}
                        {item.duration_ms !== null ? <span>{item.duration_ms} ms</span> : null}
                      </div>

                      {item.body ? (
                        <p className="mt-3 whitespace-pre-wrap text-sm text-fg-secondary">
                          {item.body}
                        </p>
                      ) : null}
                    </div>

                    {item.session_id ? (
                      <div className="flex shrink-0 flex-wrap gap-2">
                        <Button
                          size="sm"
                          variant={activeSessionId === item.session_id ? "secondary" : "primary"}
                          onClick={() => onSelectSession(item.session_id!)}
                        >
                          {activeSessionId === item.session_id ? "Viewing session chat" : "Open session chat"}
                        </Button>
                        <Link
                          href={`/dashboard/sessions/detail?id=${item.session_id}`}
                          className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-panel px-3 py-1 text-xs font-medium text-fg-primary transition-colors hover:bg-bg-hover"
                        >
                          Full session <ChevronRight size={13} />
                        </Link>
                      </div>
                    ) : null}
                  </div>

                  {item.metadata && Object.keys(item.metadata).length > 0 ? (
                    <details className="mt-3 rounded-lg border border-border-subtle bg-bg-panel/70 px-3 py-2">
                      <summary className="cursor-pointer text-xs font-medium text-fg-secondary">
                        Context
                      </summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-fg-muted">
                        {prettyJson(item.metadata)}
                      </pre>
                    </details>
                  ) : null}

                  {item.json_payload && Object.keys(item.json_payload).length > 0 ? (
                    <details className="mt-3 rounded-lg border border-border-subtle bg-bg-panel/70 px-3 py-2">
                      <summary className="cursor-pointer text-xs font-medium text-fg-secondary">
                        {item.lane === "evidence" ? "Payload" : "Result"}
                      </summary>
                      <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs text-fg-muted">
                        {prettyJson(item.json_payload)}
                      </pre>
                    </details>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
