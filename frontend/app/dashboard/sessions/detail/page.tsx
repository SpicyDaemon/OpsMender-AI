"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Brain,
  CheckCircle2,
  CircleDot,
  Clock,
  ClipboardCopy,
  Eye,
  GitBranch,
  MessageSquare,
  Search,
  Send,
  Shield,
  Terminal,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react";
import {
  approveRequest,
  connectSessionStream,
  getIncident,
  getSession,
  listApprovals,
  listMCPServers,
  listSessionMessages,
  rollbackSession,
  rejectRequest,
  sendSessionMessage,
} from "@/lib/api";
import type {
  ApprovalRequestResponse,
  IncidentResponse,
  MCPServerResponse,
  SessionRollbackResponse,
  SessionMessageResponse,
  SessionResponse,
  WSMessage,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Label, Select } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { SessionDetailSkeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/context/auth";

// ---------------------------------------------------------------------------
// Lightweight markdown renderer for co-pilot chat
// ---------------------------------------------------------------------------

function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const result: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    // Fenced code block
    const fenceMatch = lines[i].match(/^```(\w*)/);
    if (fenceMatch) {
      const lang = fenceMatch[1] || "text";
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      const code = codeLines.join("\n");
      result.push(
        <CodeBlock key={`cb-${result.length}`} code={code} language={lang} />,
      );
      continue;
    }
    // Inline code + bold + italic in a paragraph line
    result.push(
      <span key={`ln-${result.length}`} className="block">
        {renderInline(lines[i])}
      </span>,
    );
    i++;
  }
  return result;
}

function renderInline(text: string): React.ReactNode[] {
  // Split by inline code first, then bold/italic
  const parts: React.ReactNode[] = [];
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const m = match[0];
    if (m.startsWith("`")) {
      parts.push(
        <code
          key={`ic-${parts.length}`}
          className="rounded bg-bg-elevated px-1.5 py-0.5 text-[12px] font-mono text-accent"
        >
          {m.slice(1, -1)}
        </code>,
      );
    } else if (m.startsWith("**")) {
      parts.push(
        <strong key={`b-${parts.length}`} className="font-semibold">
          {m.slice(2, -2)}
        </strong>,
      );
    } else if (m.startsWith("*")) {
      parts.push(
        <em key={`i-${parts.length}`}>{m.slice(1, -1)}</em>,
      );
    }
    lastIndex = match.index + m.length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="my-2 rounded-lg border border-border-subtle bg-bg-elevated overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border-subtle bg-bg-panel">
        <span className="text-[10px] font-mono text-fg-muted uppercase tracking-wide">
          {language}
        </span>
        <button
          onClick={handleCopy}
          className="inline-flex items-center gap-1 text-[10px] text-fg-muted hover:text-fg-primary transition-colors"
        >
          <ClipboardCopy size={10} />
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre className="px-3 py-2.5 overflow-x-auto text-[12px] leading-relaxed font-mono text-fg-primary">
        {code}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Event log types
// ---------------------------------------------------------------------------

type EventKind = "node" | "tool" | "approval" | "error" | "end" | "llm" | "tier_gate";

interface LogEvent {
  id: number;
  kind: EventKind;
  label: string;
  detail?: string;
  ts: Date;
  durationMs?: number;
  raw?: Record<string, unknown>;
}

function parseWSMessage(msg: WSMessage, idGen: () => number): LogEvent | null {
  // Chat messages are handled separately — skip from the event log.
  if (msg.type === "chat_message_user" || msg.type === "chat_message_assistant") {
    return null;
  }
  const ts = new Date();
  switch (msg.type) {
    case "node_transition": {
      const node = String(msg.data.node ?? "unknown");
      const kind: EventKind = node === "tier_gate" ? "tier_gate" : "node";
      return {
        id: idGen(),
        kind,
        label: node === "tier_gate" ? "Tier Gate" : `${node.charAt(0).toUpperCase()}${node.slice(1)}`,
        detail: msg.data.status as string | undefined,
        ts,
        durationMs: typeof msg.data.duration_ms === "number" ? msg.data.duration_ms : undefined,
        raw: msg.data,
      };
    }
    case "tool_call":
      return {
        id: idGen(),
        kind: "tool",
        label: `${msg.data.tool_name ?? "unknown"}`,
        detail:
          msg.data.permitted === false
            ? `BLOCKED — ${msg.data.block_reason ?? ""}`
            : JSON.stringify(msg.data.parameters ?? {}, null, 2),
        ts,
        durationMs: typeof msg.data.duration_ms === "number" ? msg.data.duration_ms : undefined,
        raw: msg.data,
      };
    case "approval_requested":
      return {
        id: idGen(),
        kind: "approval",
        label: "Approval requested",
        detail: `ID: ${msg.data.approval_id}`,
        ts,
        raw: msg.data,
      };
    case "approval_resolved":
      return {
        id: idGen(),
        kind: "approval",
        label: `Approval ${msg.data.status}`,
        detail: String(msg.data.approval_id ?? ""),
        ts,
        raw: msg.data,
      };
    case "error":
      return {
        id: idGen(),
        kind: "error",
        label: "Error",
        detail: String(
          msg.data.detail ?? msg.data.message ?? msg.data.error ?? JSON.stringify(msg.data),
        ),
        ts,
        raw: msg.data,
      };
    case "session_end":
      return {
        id: idGen(),
        kind: "end",
        label: "Session ended",
        detail: msg.data.summary as string | undefined,
        ts,
        raw: msg.data,
      };
    default:
      return {
        id: idGen(),
        kind: "node",
        label: msg.type,
        ts,
        raw: msg.data,
      };
  }
}

const KIND_STYLES: Record<EventKind, { icon: React.ReactNode; line: string; bg: string }> = {
  node: {
    icon: <CircleDot size={14} className="text-accent" />,
    line: "border-accent/60",
    bg: "bg-accent/5",
  },
  tier_gate: {
    icon: <Shield size={14} className="text-status-medium" />,
    line: "border-status-medium/60",
    bg: "bg-status-medium/5",
  },
  tool: {
    icon: <Wrench size={14} className="text-fg-secondary" />,
    line: "border-border-strong",
    bg: "",
  },
  approval: {
    icon: <Clock size={14} className="text-status-medium" />,
    line: "border-status-medium/60",
    bg: "bg-status-medium/5",
  },
  error: {
    icon: <XCircle size={14} className="text-status-critical" />,
    line: "border-status-critical/60",
    bg: "bg-status-critical/5",
  },
  end: {
    icon: <CheckCircle2 size={14} className="text-status-low" />,
    line: "border-status-low/60",
    bg: "bg-status-low/5",
  },
  llm: {
    icon: <Brain size={14} className="text-accent" />,
    line: "border-accent/40",
    bg: "bg-accent/5",
  },
};

/** Icons per well-known workflow node */
function nodeIcon(label: string): React.ReactNode {
  const lower = label.toLowerCase();
  if (lower.includes("observe")) return <Eye size={14} className="text-status-info" />;
  if (lower.includes("diagnose")) return <Search size={14} className="text-status-high" />;
  if (lower.includes("plan")) return <GitBranch size={14} className="text-accent-hover" />;
  if (lower.includes("execute")) return <Zap size={14} className="text-status-medium" />;
  if (lower.includes("verify")) return <CheckCircle2 size={14} className="text-status-low" />;
  if (lower.includes("summarize")) return <Brain size={14} className="text-fg-secondary" />;
  return null;
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

function formatDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SessionPage() {
  return (
    <Suspense fallback={<SessionDetailSkeleton />}>
      <SessionPageContent />
    </Suspense>
  );
}

function SessionPageContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const { user } = useAuth();
  const canChat = user?.role === "admin" || user?.role === "operator";
  const canRollback = user?.role === "admin";

  const [session, setSession] = useState<SessionResponse | null>(null);
  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [messages, setMessages] = useState<SessionMessageResponse[]>([]);
  const [connected, setConnected] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequestResponse[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [timerTick, setTimerTick] = useState(0);
  const [showRollback, setShowRollback] = useState(false);

  const counterRef = useRef(0);
  const eventsBottomRef = useRef<HTMLDivElement>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const idGen = useCallback(() => ++counterRef.current, []);

  const refreshApprovals = useCallback(async () => {
    if (!id) return;
    try {
      const res = await listApprovals({ status: "pending", limit: 50 });
      setPendingApprovals(res.items.filter((a) => a.session_id === id));
    } catch {
      // ignore
    }
  }, [id]);

  // Merge a new chat message into state, dropping duplicates by id.
  const mergeMessage = useCallback((msg: SessionMessageResponse) => {
    setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
  }, []);

  // Initial load — session + incident + chat history
  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const s = await getSession(id);
        if (cancelled) return;
        setSession(s);
        if (s.incident_id) {
          try {
            const inc = await getIncident(s.incident_id);
            if (!cancelled) setIncident(inc);
          } catch {
            // ignore — incident may have been deleted
          }
        }
        const history = await listSessionMessages(id);
        if (!cancelled) setMessages(history.items);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // WebSocket
  useEffect(() => {
    if (!id) return;
    const ws = connectSessionStream(
      id,
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
    wsRef.current = ws;
    return () => ws.close();
  }, [id, idGen, mergeMessage, refreshApprovals]);

  // Auto-scroll
  useEffect(() => {
    eventsBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (
      session?.tier !== 0
      || session.status !== "active"
      || !session.tier0_max_session_seconds
    ) {
      return;
    }
    const interval = window.setInterval(() => {
      setTimerTick((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [session?.status, session?.tier, session?.tier0_max_session_seconds]);

  // Initial approval load
  useEffect(() => {
    refreshApprovals();
  }, [refreshApprovals]);

  async function handleApprove(approvalId: string) {
    await approveRequest(approvalId);
    setPendingApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    setEvents((prev) => [
      ...prev,
      { id: idGen(), kind: "approval", label: "Approval approved (by you)", ts: new Date() },
    ]);
  }

  async function handleReject(approvalId: string) {
    await rejectRequest(approvalId);
    setPendingApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    setEvents((prev) => [
      ...prev,
      { id: idGen(), kind: "approval", label: "Approval rejected (by you)", ts: new Date() },
    ]);
  }

  async function handleSend() {
    const content = draft.trim();
    if (!content || sending || !id) return;
    setSendError("");
    setSending(true);
    try {
      const saved = await sendSessionMessage(id, { content });
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
    return "Add context or ask anything…";
  }, [canChat, session?.status]);

  const tier0Timer = useMemo(() => {
    if (!session || session.tier !== 0 || !session.tier0_max_session_seconds) {
      return null;
    }
    const startedMs = new Date(session.started_at).getTime();
    const deadlineMs = startedMs + session.tier0_max_session_seconds * 1000;
    const nowMs = Date.now() + timerTick * 0;
    const remainingSeconds = Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
    const expired = remainingSeconds <= 0;
    const label =
      session.status === "timed_out"
        ? "Time limit reached"
        : expired && session.status === "active"
          ? "Time limit reached"
          : `Tier 0 time left ${formatDuration(remainingSeconds)}`;
    return {
      expired,
      label,
      maxSessionSeconds: session.tier0_max_session_seconds,
    };
  }, [session, timerTick]);

  if (loading) return <SessionDetailSkeleton />;
  if (!id) return <p className="text-status-critical">Missing session id.</p>;
  if (!session) return <p className="text-status-critical">Session not found.</p>;

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Back + incident context strip */}
      <div>
        <Link
          href="/dashboard/incidents"
          className="inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg-primary mb-3 transition-colors"
        >
          <ArrowLeft size={14} /> Incidents
        </Link>

        <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                {incident ? (
                  <Link
                    href={`/dashboard/incidents/detail?id=${incident.id}`}
                    className="text-sm font-semibold text-fg-primary truncate hover:underline"
                  >
                    {incident.title}
                  </Link>
                ) : (
                  <span className="text-sm font-semibold text-fg-secondary">
                    No incident linked
                  </span>
                )}
                {incident?.severity && (
                  <Badge variant={incident.severity as Parameters<typeof Badge>[0]["variant"]}>
                    {incident.severity}
                  </Badge>
                )}
                {incident && (
                  <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
                    {incident.status.replace("_", " ")}
                  </Badge>
                )}
                <Badge variant={session.status as Parameters<typeof Badge>[0]["variant"]}>
                  {session.status.replace("_", " ")}
                </Badge>
                <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
                  <Shield size={11} />
                  Tier {session.tier}
                </span>
                {tier0Timer && (
                  <Badge variant={tier0Timer.expired ? "failed" : "pending"}>
                    <Clock size={10} className="mr-1" />
                    {tier0Timer.label}
                  </Badge>
                )}
                {session.model_provider && (
                  <span className="text-xs text-fg-muted font-mono">
                    {session.model_provider}/{session.model_id ?? "default"}
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-xs text-fg-muted font-mono tabular-nums">
                session {session.id.slice(0, 8)}…
                {session.started_at && (
                  <span className="ml-2">
                    started {new Date(session.started_at).toLocaleTimeString()}
                  </span>
                )}
              </p>
            </div>
            <span
              className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full shrink-0 ${
                connected ? "bg-status-low-bg text-status-low" : "bg-bg-elevated text-fg-secondary"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connected ? "bg-status-low animate-pulse" : "bg-bg-elevated"
                }`}
              />
              {connected ? "Live" : "Disconnected"}
            </span>
          </div>
          {canRollback && session.tier === 0 && (
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-border-subtle pt-3">
              <p className="text-xs text-fg-secondary">
                Tier 0 sessions can replay compensating inverses in reverse order.
              </p>
              <Button size="sm" variant="secondary" onClick={() => setShowRollback(true)}>
                Rollback Session
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Pending approvals (above the split so they're always visible) */}
      {pendingApprovals.length > 0 && (
        <div className="rounded-xl border border-status-medium-border bg-status-medium-bg/40 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Clock size={16} className="text-status-medium" />
            <p className="text-sm font-semibold text-status-medium">
              {pendingApprovals.length} pending approval
              {pendingApprovals.length > 1 ? "s" : ""}
            </p>
          </div>
          {pendingApprovals.map((a) => (
            <div
              key={a.id}
              className="flex items-start gap-4 rounded-lg bg-bg-panel border border-status-medium-border/60 p-4"
            >
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-fg-secondary uppercase tracking-wide mb-1.5">
                  Action context
                </p>
                <pre className="text-xs text-fg-primary bg-bg-elevated rounded-lg p-3 overflow-x-auto font-mono">
                  {JSON.stringify(a.action, null, 2)}
                </pre>
                {a.justification && (
                  <p className="text-xs text-fg-secondary mt-2">
                    <span className="font-medium text-fg-muted">Reason:</span> {a.justification}
                  </p>
                )}
                <p className="text-xs text-fg-muted mt-1.5 tabular-nums font-mono">
                  Expires {new Date(a.expires_at).toLocaleTimeString()}
                </p>
              </div>
              <div className="flex flex-col gap-2 shrink-0">
                <Button size="sm" variant="success" onClick={() => handleApprove(a.id)} className="min-w-[100px]">
                  <CheckCircle2 size={14} />
                  Approve
                </Button>
                <Button size="sm" variant="danger" onClick={() => handleReject(a.id)} className="min-w-[100px]">
                  <XCircle size={14} />
                  Reject
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Split view: event stream + co-pilot chat */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4 min-h-0">
        {/* Event stream */}
        <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm min-h-0">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
            <Terminal size={14} className="text-fg-muted" />
            <span className="text-xs font-medium text-fg-secondary uppercase tracking-wide">
              Event Stream
            </span>
            <span className="ml-auto text-[10px] text-fg-muted tabular-nums font-mono">
              {events.length} events
            </span>
          </div>

          <div className="flex-1 overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-fg-muted">
                <CircleDot size={24} className="mb-2 animate-pulse" />
                <p className="text-sm">Waiting for events…</p>
              </div>
            ) : (
              <div className="divide-y divide-border-subtle/50">
                {events.map((ev) => {
                  const style = KIND_STYLES[ev.kind];
                  const customIcon = ev.kind === "node" ? nodeIcon(ev.label) : null;
                  return (
                    <div
                      key={ev.id}
                      className={`flex gap-3 px-4 py-3 border-l-2 ${style.line} ${style.bg} transition-colors hover:bg-bg-hover/40`}
                    >
                      <div className="mt-0.5 shrink-0">{customIcon ?? style.icon}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium text-fg-primary">{ev.label}</p>
                            {ev.durationMs != null && (
                              <span className="text-[10px] text-fg-muted font-mono tabular-nums bg-bg-elevated rounded px-1.5 py-0.5">
                                {formatMs(ev.durationMs)}
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] text-fg-muted shrink-0 tabular-nums font-mono">
                            {ev.ts.toLocaleTimeString()}
                          </span>
                        </div>
                        {ev.detail && (
                          <pre className="mt-1.5 text-xs text-fg-secondary whitespace-pre-wrap break-words font-mono leading-relaxed">
                            {ev.detail}
                          </pre>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div ref={eventsBottomRef} />
              </div>
            )}
          </div>

          {session.summary && (
            <div className="border-t border-status-low-border bg-status-low-bg/40 px-4 py-3">
              <p className="text-[10px] font-semibold text-status-low uppercase tracking-wide mb-1.5">
                Summary
              </p>
              <p className="text-sm text-status-low whitespace-pre-wrap leading-relaxed">
                {session.summary}
              </p>
            </div>
          )}
        </div>

        {/* Co-pilot chat */}
        <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm min-h-0">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
            <MessageSquare size={14} className="text-accent" />
            <span className="text-xs font-medium text-fg-secondary uppercase tracking-wide">
              Co-pilot
            </span>
            <span className="ml-auto text-[10px] text-fg-muted tabular-nums font-mono">
              {messages.length} messages
            </span>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3 bg-bg-elevated/30">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-fg-muted">
                <MessageSquare size={22} className="mb-2 opacity-60" />
                <p className="text-sm">No messages yet.</p>
                {canChat && (
                  <p className="text-xs mt-1">Ask the co-pilot anything about this incident.</p>
                )}
              </div>
            ) : (
              messages.map((m) => (
                <ChatBubble key={m.id} message={m} />
              ))
            )}
            <div ref={chatBottomRef} />
          </div>

          <div className="border-t border-border-subtle p-3">
            {sendError && (
              <p className="text-xs text-status-critical mb-2">{sendError}</p>
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
                className="flex-1 resize-none rounded-lg border border-border-subtle bg-bg-input px-3 py-2 text-sm shadow-sm placeholder:text-fg-muted focus:border-accent focus:ring-1 focus:ring-accent disabled:bg-bg-elevated disabled:text-fg-muted transition-colors"
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

      {canRollback && session.tier === 0 && (
        <RollbackModal
          open={showRollback}
          onClose={() => setShowRollback(false)}
          session={session}
          onRollbackComplete={(report) => {
            setEvents((prev) => [
              ...prev,
              {
                id: idGen(),
                kind: report.failed > 0 ? "error" : "tool",
                label: report.dry_run ? "Rollback preview generated" : "Rollback executed",
                detail: `${report.succeeded} succeeded, ${report.failed} failed, ${report.skipped} skipped`,
                ts: new Date(),
              },
            ]);
          }}
        />
      )}
    </div>
  );
}

function RollbackModal({
  open,
  onClose,
  session,
  onRollbackComplete,
}: {
  open: boolean;
  onClose: () => void;
  session: SessionResponse;
  onRollbackComplete: (report: SessionRollbackResponse) => void;
}) {
  const [servers, setServers] = useState<MCPServerResponse[]>([]);
  const [selectedServer, setSelectedServer] = useState("");
  const [loadingServers, setLoadingServers] = useState(false);
  const [submitting, setSubmitting] = useState<"preview" | "live" | "">("");
  const [error, setError] = useState("");
  const [report, setReport] = useState<SessionRollbackResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingServers(true);
    setError("");
    listMCPServers()
      .then((res) => {
        if (cancelled) return;
        const active = res.items.filter((item) => item.is_active);
        setServers(active);
        setSelectedServer((current) => {
          if (current && active.some((item) => item.name === current)) return current;
          return active[0]?.name ?? "";
        });
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load MCP servers");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingServers(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function runRollback(dryRun: boolean) {
    setError("");
    setSubmitting(dryRun ? "preview" : "live");
    try {
      const result = await rollbackSession(session.id, {
        dry_run: dryRun,
        mcp_server: dryRun ? selectedServer || undefined : selectedServer,
      });
      setReport(result);
      onRollbackComplete(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed");
    } finally {
      setSubmitting("");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Rollback Session">
      <div className="space-y-4">
        <div className="rounded-lg border border-border-subtle bg-bg-elevated p-3 text-sm text-fg-secondary">
          Preview resolves the rollback plan from audit + skill metadata.
          Run rollback replays compensating inverses against the selected MCP server.
        </div>

        <div>
          <Label htmlFor="rollback-server">MCP Server</Label>
          <Select
            id="rollback-server"
            value={selectedServer}
            onChange={(e) => setSelectedServer(e.target.value)}
            disabled={loadingServers || servers.length === 0}
          >
            <option value="">Select a server</option>
            {servers.map((server) => (
              <option key={server.id} value={server.name}>
                {server.name} ({server.transport})
              </option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-fg-muted">
            Live rollback requires the same MCP surface the original actions ran against.
          </p>
        </div>

        {error && <p className="text-sm text-status-critical">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="secondary"
            onClick={() => runRollback(true)}
            loading={submitting === "preview"}
          >
            Preview Rollback
          </Button>
          <Button
            variant="danger"
            onClick={() => runRollback(false)}
            loading={submitting === "live"}
            disabled={!selectedServer}
          >
            Run Rollback
          </Button>
        </div>

        {report && (
          <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-panel p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={report.failed > 0 ? "failed" : "approved"}>
                {report.dry_run ? "Preview" : "Executed"}
              </Badge>
              <span className="text-xs text-fg-secondary">
                attempted {report.attempted} · succeeded {report.succeeded} · failed {report.failed} · skipped {report.skipped}
              </span>
            </div>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {report.steps.map((step, index) => (
                <div key={`${step.original_tool}-${index}`} className="rounded-md border border-border-subtle bg-bg-elevated p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-fg-primary">
                      {step.original_tool}
                    </span>
                    <span className="text-xs text-fg-muted">→</span>
                    <span className="text-sm text-fg-secondary">
                      {step.inverse_tool ?? "no inverse"}
                    </span>
                    <Badge
                      variant={
                        step.status === "succeeded"
                          ? "approved"
                          : step.status === "failed"
                            ? "failed"
                            : "pending"
                      }
                    >
                      {step.status.replaceAll("_", " ")}
                    </Badge>
                  </div>
                  {Object.keys(step.parameters).length > 0 && (
                    <pre className="mt-2 text-xs text-fg-secondary whitespace-pre-wrap break-words font-mono">
                      {JSON.stringify(step.parameters, null, 2)}
                    </pre>
                  )}
                  {step.error && (
                    <p className="mt-2 text-xs text-status-critical">{step.error}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// ChatBubble — with markdown rendering, code blocks, copy buttons
// ---------------------------------------------------------------------------

function ChatBubble({ message }: { message: SessionMessageResponse }) {
  const isUser = message.role === "user";
  const ts = new Date(message.created_at);
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm shadow-sm ${
          isUser
            ? "bg-accent/90 text-fg-primary rounded-br-sm"
            : "bg-bg-panel text-fg-primary border border-border-subtle rounded-bl-sm"
        }`}
      >
        <div className="leading-relaxed">
          {isUser ? message.content : renderMarkdown(message.content)}
        </div>
        <div
          className={`mt-1.5 flex items-center gap-1.5 text-[10px] ${
            isUser ? "text-fg-primary/60" : "text-fg-muted"
          }`}
        >
          <span className="tabular-nums font-mono">{ts.toLocaleTimeString()}</span>
          {message.node_context && (
            <span className="opacity-75">· {message.node_context}</span>
          )}
        </div>
      </div>
    </div>
  );
}
