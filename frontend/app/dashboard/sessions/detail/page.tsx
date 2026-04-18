"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  CircleDot,
  Clock,
  MessageSquare,
  Send,
  Terminal,
  XCircle,
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
import { PageSpinner } from "@/components/ui/Spinner";
import { useAuth } from "@/context/auth";

// ---------------------------------------------------------------------------
// Event log types
// ---------------------------------------------------------------------------

type EventKind = "node" | "tool" | "approval" | "error" | "end" | "llm";

interface LogEvent {
  id: number;
  kind: EventKind;
  label: string;
  detail?: string;
  ts: Date;
  raw?: Record<string, unknown>;
}

function parseWSMessage(msg: WSMessage, idGen: () => number): LogEvent | null {
  // Chat messages are handled separately — skip from the event log.
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
        raw: msg.data,
      };
    case "tool_call":
      return {
        id: idGen(),
        kind: "tool",
        label: `Tool: ${msg.data.tool_name ?? "unknown"}`,
        detail:
          msg.data.permitted === false
            ? `BLOCKED — ${msg.data.block_reason ?? ""}`
            : JSON.stringify(msg.data.parameters ?? {}, null, 2),
        ts,
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

const KIND_STYLES: Record<EventKind, { icon: React.ReactNode; line: string }> = {
  node: { icon: <CircleDot size={14} className="text-indigo-500" />, line: "border-indigo-100" },
  tool: { icon: <Terminal size={14} className="text-gray-500" />, line: "border-gray-200" },
  approval: { icon: <Clock size={14} className="text-yellow-500" />, line: "border-yellow-100" },
  error: { icon: <XCircle size={14} className="text-red-500" />, line: "border-red-100" },
  end: { icon: <CheckCircle2 size={14} className="text-green-500" />, line: "border-green-100" },
  llm: { icon: <CircleDot size={14} className="text-purple-500" />, line: "border-purple-100" },
};

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

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SessionPage() {
  return (
    <Suspense fallback={<PageSpinner />}>
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

  if (loading) return <PageSpinner />;
  if (!id) return <p className="text-red-600">Missing session id.</p>;
  if (!session) return <p className="text-red-600">Session not found.</p>;

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Back + incident context strip */}
      <div>
        <Link
          href="/dashboard/incidents"
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900 mb-3"
        >
          <ArrowLeft size={14} /> Incidents
        </Link>

        <div className="rounded-xl border border-gray-200 bg-white shadow-sm px-4 py-3">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                {incident ? (
                  <Link
                    href={`/dashboard/incidents/detail?id=${incident.id}`}
                    className="text-sm font-semibold text-gray-900 truncate hover:underline"
                  >
                    {incident.title}
                  </Link>
                ) : (
                  <span className="text-sm font-semibold text-gray-500">
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
                <span className="text-xs text-gray-400">Tier {session.tier}</span>
                {tier0Timer && (
                  <Badge variant={tier0Timer.expired ? "failed" : "pending"}>
                    {tier0Timer.label}
                  </Badge>
                )}
                {session.model_provider && (
                  <span className="text-xs text-gray-400">
                    {session.model_provider}/{session.model_id ?? "default"}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-gray-400 font-mono">
                session {session.id.slice(0, 8)}…
              </p>
            </div>
            <span
              className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full shrink-0 ${
                connected ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connected ? "bg-green-500 animate-pulse" : "bg-gray-400"
                }`}
              />
              {connected ? "Live" : "Disconnected"}
            </span>
          </div>
          {canRollback && session.tier === 0 && (
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-gray-100 pt-3">
              <p className="text-xs text-gray-500">
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
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 space-y-2">
          <p className="text-sm font-semibold text-yellow-800">
            {pendingApprovals.length} pending approval
            {pendingApprovals.length > 1 ? "s" : ""}
          </p>
          {pendingApprovals.map((a) => (
            <div
              key={a.id}
              className="flex items-start gap-3 rounded-lg bg-white border border-yellow-200 p-3"
            >
              <div className="flex-1 min-w-0">
                <pre className="text-xs text-gray-700 bg-gray-50 rounded p-2 overflow-x-auto">
                  {JSON.stringify(a.action, null, 2)}
                </pre>
                {a.justification && (
                  <p className="text-xs text-gray-500 mt-1">{a.justification}</p>
                )}
                <p className="text-xs text-gray-400 mt-1">
                  Expires {new Date(a.expires_at).toLocaleTimeString()}
                </p>
              </div>
              <div className="flex flex-col gap-1.5 shrink-0">
                <Button size="sm" variant="success" onClick={() => handleApprove(a.id)}>
                  Approve
                </Button>
                <Button size="sm" variant="danger" onClick={() => handleReject(a.id)}>
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
        <div className="flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm min-h-0">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <Terminal size={14} className="text-gray-400" />
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Event Stream
            </span>
          </div>

          <div className="flex-1 overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-gray-400">
                <CircleDot size={24} className="mb-2 animate-pulse" />
                <p className="text-sm">Waiting for events…</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-50">
                {events.map((ev) => {
                  const { icon, line } = KIND_STYLES[ev.kind];
                  return (
                    <div
                      key={ev.id}
                      className={`flex gap-3 px-4 py-3 border-l-2 ${line}`}
                    >
                      <div className="mt-0.5 shrink-0">{icon}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline justify-between gap-2">
                          <p className="text-sm font-medium text-gray-800">{ev.label}</p>
                          <span className="text-xs text-gray-400 shrink-0">
                            {ev.ts.toLocaleTimeString()}
                          </span>
                        </div>
                        {ev.detail && (
                          <pre className="mt-1 text-xs text-gray-500 whitespace-pre-wrap break-words font-mono">
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
            <div className="border-t border-green-200 bg-green-50 px-4 py-3">
              <p className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-1">
                Summary
              </p>
              <p className="text-sm text-green-800 whitespace-pre-wrap">
                {session.summary}
              </p>
            </div>
          )}
        </div>

        {/* Co-pilot chat */}
        <div className="flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm min-h-0">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <MessageSquare size={14} className="text-indigo-500" />
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Co-pilot
            </span>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2 bg-gray-50/40">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-gray-400">
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

          <div className="border-t border-gray-100 p-3">
            {sendError && (
              <p className="text-xs text-red-600 mb-2">{sendError}</p>
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
                className="flex-1 resize-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200 disabled:bg-gray-50 disabled:text-gray-400"
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
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
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
          <p className="mt-1 text-xs text-gray-400">
            Live rollback requires the same MCP surface the original actions ran against.
          </p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

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
          <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={report.failed > 0 ? "failed" : "approved"}>
                {report.dry_run ? "Preview" : "Executed"}
              </Badge>
              <span className="text-xs text-gray-500">
                attempted {report.attempted} · succeeded {report.succeeded} · failed {report.failed} · skipped {report.skipped}
              </span>
            </div>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {report.steps.map((step, index) => (
                <div key={`${step.original_tool}-${index}`} className="rounded-md border border-gray-200 bg-gray-50 p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-gray-800">
                      {step.original_tool}
                    </span>
                    <span className="text-xs text-gray-400">→</span>
                    <span className="text-sm text-gray-600">
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
                    <pre className="mt-2 text-xs text-gray-500 whitespace-pre-wrap break-words font-mono">
                      {JSON.stringify(step.parameters, null, 2)}
                    </pre>
                  )}
                  {step.error && (
                    <p className="mt-2 text-xs text-red-600">{step.error}</p>
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
// ChatBubble
// ---------------------------------------------------------------------------

function ChatBubble({ message }: { message: SessionMessageResponse }) {
  const isUser = message.role === "user";
  const ts = new Date(message.created_at);
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words shadow-sm ${
          isUser
            ? "bg-indigo-600 text-white"
            : "bg-white text-gray-800 border border-gray-200"
        }`}
      >
        {message.content}
        <div
          className={`mt-1 text-[10px] ${
            isUser ? "text-indigo-100" : "text-gray-400"
          }`}
        >
          {ts.toLocaleTimeString()}
          {message.node_context && (
            <span className="ml-1.5 opacity-75">· {message.node_context}</span>
          )}
        </div>
      </div>
    </div>
  );
}
