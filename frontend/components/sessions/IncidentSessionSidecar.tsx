"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  MessageSquare,
  Send,
  Terminal,
  X,
  XCircle,
} from "lucide-react";
import {
  approveRequest,
  connectSessionStream,
  getSession,
  listApprovals,
  listSessionMessages,
  rejectRequest,
  sendSessionMessage,
} from "@/lib/api";
import type {
  ApprovalRequestResponse,
  SessionMessageResponse,
  SessionResponse,
  WSMessage,
} from "@/lib/types";
import { useAuth } from "@/context/auth";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { formatTime } from "@/lib/formatDate";

type EventKind = "node" | "tool" | "approval" | "error" | "end";

interface LogEvent {
  id: number;
  kind: EventKind;
  label: string;
  detail?: string;
  ts: Date;
}

function parseWSMessage(msg: WSMessage, idGen: () => number): LogEvent | null {
  if (msg.type === "chat_message_user" || msg.type === "chat_message_assistant") {
    return null;
  }
  const ts = new Date();
  switch (msg.type) {
    case "node_transition":
      return {
        id: idGen(),
        kind: "node",
        label: `Node: ${msg.data.node ?? "unknown"}`,
        detail: msg.data.status as string | undefined,
        ts,
      };
    case "tool_call":
      return {
        id: idGen(),
        kind: "tool",
        label: `Tool: ${msg.data.tool_name ?? "unknown"}`,
        detail:
          msg.data.permitted === false
            ? `Blocked — ${msg.data.block_reason ?? ""}`
            : JSON.stringify(msg.data.parameters ?? {}, null, 2),
        ts,
      };
    case "approval_requested":
      return {
        id: idGen(),
        kind: "approval",
        label: "Approval requested",
        detail: `ID: ${msg.data.approval_id ?? ""}`,
        ts,
      };
    case "approval_resolved":
      return {
        id: idGen(),
        kind: "approval",
        label: `Approval ${msg.data.status ?? "resolved"}`,
        detail: String(msg.data.approval_id ?? ""),
        ts,
      };
    case "error":
      return {
        id: idGen(),
        kind: "error",
        label: "Error",
        detail: String(
          msg.data.detail ?? msg.data.message ?? msg.data.error ?? "Unknown error",
        ),
        ts,
      };
    case "session_end":
      return {
        id: idGen(),
        kind: "end",
        label: "Session ended",
        detail: msg.data.summary as string | undefined,
        ts,
      };
    default:
      return null;
  }
}

function chatMessageFromWS(msg: WSMessage): SessionMessageResponse | null {
  if (msg.type !== "chat_message_user" && msg.type !== "chat_message_assistant") {
    return null;
  }
  const d = msg.data as Record<string, unknown>;
  if (typeof d.id !== "string" || typeof d.content !== "string") return null;
  return {
    id: d.id,
    session_id: String(d.session_id ?? ""),
    role: msg.type === "chat_message_user" ? "user" : "assistant",
    content: d.content,
    created_at: String(d.created_at ?? new Date().toISOString()),
    consumed_by_workflow: false,
    node_context: (d.node_context as string | null) ?? null,
  };
}

const KIND_STYLES: Record<EventKind, { icon: React.ReactNode; accent: string }> = {
  node: { icon: <CircleDot size={13} className="text-accent-text" />, accent: "border-accent" },
  tool: { icon: <Terminal size={13} className="text-fg-secondary" />, accent: "border-border-subtle" },
  approval: { icon: <Clock3 size={13} className="text-status-high" />, accent: "border-status-high-border" },
  error: { icon: <XCircle size={13} className="text-status-critical" />, accent: "border-status-critical-border" },
  end: { icon: <CheckCircle2 size={13} className="text-status-low" />, accent: "border-status-low-border" },
};

function displayStatus(value: string | null | undefined): string {
  if (!value) return "Unknown";
  return value
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function IncidentSessionSidecar({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const toast = useToast();
  const canChat = user?.role === "admin" || user?.role === "operator";
  const canApprove = canChat;

  const [session, setSession] = useState<SessionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [messages, setMessages] = useState<SessionMessageResponse[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequestResponse[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const counterRef = useRef(0);
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const eventsBottomRef = useRef<HTMLDivElement>(null);

  const idGen = useCallback(() => ++counterRef.current, []);

  const refreshApprovals = useCallback(async () => {
    try {
      const res = await listApprovals({ status: "pending", limit: 50 });
      setPendingApprovals(res.items.filter((item) => item.session_id === sessionId));
    } catch {
      // keep panel usable even if approvals fail to refresh
    }
  }, [sessionId]);

  const mergeMessage = useCallback((msg: SessionMessageResponse) => {
    setMessages((prev) => (prev.some((item) => item.id === msg.id) ? prev : [...prev, msg]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [sessionRes, history] = await Promise.all([
          getSession(sessionId),
          listSessionMessages(sessionId),
        ]);
        if (cancelled) return;
        setSession(sessionRes);
        setMessages(history.items);
      } catch (err) {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : "Failed to load session");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    refreshApprovals();
    return () => {
      cancelled = true;
    };
  }, [refreshApprovals, sessionId, toast]);

  useEffect(() => {
    const ws = connectSessionStream(
      sessionId,
      (msg) => {
        const chat = chatMessageFromWS(msg);
        if (chat) {
          mergeMessage(chat);
          return;
        }
        const ev = parseWSMessage(msg, idGen);
        if (ev) setEvents((prev) => [...prev, ev]);
        if (msg.type === "approval_requested") {
          setSession((prev) => (prev ? { ...prev, status: "awaiting_approval" } : prev));
          refreshApprovals();
        }
        if (msg.type === "approval_resolved") {
          const resolution = String(msg.data.status ?? "");
          setSession((prev) => {
            if (!prev) return prev;
            if (resolution === "expired") {
              return { ...prev, status: "timed_out", ended_at: new Date().toISOString() };
            }
            return { ...prev, status: "active" };
          });
          refreshApprovals();
        }
        if (msg.type === "session_end") {
          setSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: (msg.data.status as SessionResponse["status"]) ?? prev.status,
                  summary: (msg.data.summary as string | null | undefined) ?? prev.summary,
                  ended_at: prev.ended_at ?? new Date().toISOString(),
                }
              : prev,
          );
        }
      },
      () => setConnected(false),
    );
    ws.onopen = () => setConnected(true);
    return () => ws.close();
  }, [idGen, mergeMessage, refreshApprovals, sessionId]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    eventsBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  async function handleApprove(approvalId: string) {
    try {
      await approveRequest(approvalId);
      setPendingApprovals((prev) => prev.filter((item) => item.id !== approvalId));
      toast.success("Approval granted.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to approve request");
    }
  }

  async function handleReject(approvalId: string) {
    try {
      await rejectRequest(approvalId);
      setPendingApprovals((prev) => prev.filter((item) => item.id !== approvalId));
      toast.success("Approval rejected.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reject request");
    }
  }

  async function handleSend() {
    const content = draft.trim();
    if (!content || sending) return;
    setSendError("");
    setSending(true);
    try {
      const saved = await sendSessionMessage(sessionId, { content });
      mergeMessage(saved);
      setDraft("");
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  }

  const inputPlaceholder = useMemo(() => {
    if (!canChat) return "Chat is read-only for viewers.";
    if (session?.status === "completed") return "Session complete — chat is still open.";
    return "Ask the co-pilot or add context…";
  }, [canChat, session?.status]);

  return (
    <aside className="flex min-h-[600px] flex-col overflow-hidden rounded-2xl border border-border-subtle bg-bg-panel shadow-sm xl:sticky xl:top-8 xl:max-h-[calc(100vh-8rem)]">
      <div className="border-b border-border-subtle px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold text-fg-primary">Session Chat</p>
              {session && (
                <Badge variant={session.status as Parameters<typeof Badge>[0]["variant"]}>
                  {displayStatus(session.status)}
                </Badge>
              )}
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                  connected
                    ? "bg-status-low-bg text-status-low"
                    : "bg-bg-elevated text-fg-secondary"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    connected ? "bg-status-low animate-pulse" : "bg-fg-muted"
                  }`}
                />
                {connected ? "Live" : "Offline"}
              </span>
            </div>
            <p className="mt-1 font-mono text-xs text-fg-muted">
              {loading ? "Loading session…" : `session ${sessionId.slice(0, 8)}…`}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg-primary"
            aria-label="Close session chat panel"
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {session && (
            <span className="text-xs text-fg-secondary">
              Tier {session.tier}
              {session.model_provider
                ? ` • ${session.model_provider}/${session.model_id ?? "default"}`
                : ""}
            </span>
          )}
          <Link
            href={`/dashboard/sessions/detail?id=${sessionId}`}
            className="inline-flex items-center gap-1 text-xs font-medium text-accent-text transition-colors hover:text-accent-hover"
          >
            Open full session view <ExternalLink size={12} />
          </Link>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-hidden p-4">
        {pendingApprovals.length > 0 && (
          <section className="rounded-xl border border-status-high-border bg-status-high-bg p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-status-high">
                {pendingApprovals.length} pending approval
                {pendingApprovals.length === 1 ? "" : "s"}
              </p>
              <Badge variant="pending">Action Needed</Badge>
            </div>
            <div className="mt-3 space-y-2">
              {pendingApprovals.map((approval) => (
                <div
                  key={approval.id}
                  className="rounded-lg border border-status-high-border bg-bg-panel p-3"
                >
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs text-fg-secondary">
                    {JSON.stringify(approval.action, null, 2)}
                  </pre>
                  {approval.justification && (
                    <p className="mt-2 text-xs text-fg-secondary">{approval.justification}</p>
                  )}
                  {canApprove && (
                    <div className="mt-3 flex gap-2">
                      <Button size="sm" variant="success" onClick={() => handleApprove(approval.id)}>
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
                        onClick={() => handleReject(approval.id)}
                      >
                        Reject
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="flex min-h-0 flex-1 flex-col gap-4 xl:min-h-[0]">
          <div className="grid min-h-0 flex-1 gap-4 xl:grid-rows-[minmax(0,0.95fr)_minmax(0,1.25fr)]">
            <div className="flex min-h-0 flex-col rounded-xl border border-border-subtle bg-bg-elevated/40">
              <div className="border-b border-border-subtle px-3 py-2">
                <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                  Latest Activity
                </p>
              </div>
              <div
                className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
                tabIndex={0}
                role="log"
                aria-label="Latest session activity"
                aria-live="polite"
                aria-relevant="additions text"
              >
                {events.length === 0 ? (
                  <EmptyState
                    icon={Terminal}
                    title="Waiting for workflow events"
                    description="Node transitions, tool calls, and approval changes will appear here."
                    className="h-full py-8"
                  />
                ) : (
                  <div className="space-y-2">
                    {events.map((event) => {
                      const style = KIND_STYLES[event.kind];
                      return (
                        <div
                          key={event.id}
                          className={`rounded-lg border-l-2 bg-bg-panel px-3 py-2 ${style.accent}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-2">
                              {style.icon}
                              <p className="text-xs font-medium text-fg-primary">{event.label}</p>
                            </div>
                            <span className="text-[11px] text-fg-muted">
                              {formatTime(event.ts)}
                            </span>
                          </div>
                          {event.detail && (
                            <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] text-fg-secondary">
                              {event.detail}
                            </pre>
                          )}
                        </div>
                      );
                    })}
                    <div ref={eventsBottomRef} />
                  </div>
                )}
              </div>
            </div>

            <div className="flex min-h-0 flex-col rounded-xl border border-border-subtle bg-bg-elevated/40">
              <div className="border-b border-border-subtle px-3 py-2">
                <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                  Co-pilot Chat
                </p>
              </div>
              <div
                className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
                tabIndex={0}
                role="region"
                aria-label="Incident session co-pilot chat"
              >
                {messages.length === 0 ? (
                  <EmptyState
                    icon={MessageSquare}
                    title="No chat yet"
                    description="Use the chat panel for quick back-and-forth, then jump to the full session page for the complete operational view."
                    className="h-full py-8"
                  />
                ) : (
                  <div className="space-y-2">
                    {messages.map((message) => (
                      <ChatBubble key={message.id} message={message} />
                    ))}
                    <div ref={chatBottomRef} />
                  </div>
                )}
              </div>

              <div className="border-t border-border-subtle p-3">
                {sendError && (
                  <p className="mb-2 text-xs text-status-critical">{sendError}</p>
                )}
                <div className="flex items-end gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    disabled={!canChat}
                    placeholder={inputPlaceholder}
                    rows={2}
                    className="flex-1 resize-none rounded-lg border border-border-subtle bg-bg-panel px-3 py-2 text-sm shadow-sm placeholder:text-fg-muted focus:border-accent focus:ring-1 focus:ring-accent disabled:bg-bg-elevated disabled:text-fg-muted"
                  />
                  <Button
                    size="sm"
                    onClick={handleSend}
                    loading={sending}
                    disabled={!canChat || !draft.trim()}
                  >
                    <Send size={13} />
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {session?.summary && (
            <div className="rounded-xl border border-status-low-border bg-status-low-bg px-3 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-status-low">
                Session Summary
              </p>
              <p className="mt-1 whitespace-pre-wrap text-sm text-status-low">
                {session.summary}
              </p>
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}

function ChatBubble({ message }: { message: SessionMessageResponse }) {
  const isUser = message.role === "user";
  const ts = new Date(message.created_at);
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[92%] min-w-0 overflow-hidden rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words shadow-sm ${
          isUser
            ? "bg-accent text-accent-contrast"
            : "border border-border-subtle bg-bg-panel text-fg-primary"
        }`}
      >
        {message.content}
        <div className={`mt-1 text-[10px] ${isUser ? "text-accent-contrast" : "text-fg-muted"}`}>
          {formatTime(ts)}
          {message.node_context ? (
            <span className={`ml-1.5 ${isUser ? "" : "opacity-75"}`}>
              · {message.node_context}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}
