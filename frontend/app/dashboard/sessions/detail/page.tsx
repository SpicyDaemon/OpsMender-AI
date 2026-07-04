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
  CornerUpRight,
  Eye,
  GitBranch,
  MessageSquare,
  Search,
  Send,
  Shield,
  Split,
  StopCircle,
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
  overrideSession,
  redirectRequest,
  rollbackSession,
  rejectRequest,
  sendSessionMessage,
  stopSession,
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
import { SessionMemoriesPanel } from "@/components/SessionMemoriesPanel";
import { SessionWorkflowState } from "@/components/sessions/SessionWorkflowState";
import { TierCapabilitySummary } from "@/components/sessions/TierCapabilitySummary";
import { ToolCallCard } from "@/components/sessions/ToolCallCard";
import { titleCaseIdentifier, workflowNodeLabel } from "@/lib/displayNames";
import { formatRelative, formatTime } from "@/lib/formatDate";
import { useAuth } from "@/context/auth";

// ---------------------------------------------------------------------------
// Session status helpers
// ---------------------------------------------------------------------------

/**
 * Terminal statuses — the session has ended and nothing is live. The header
 * must not show a "Live" pill, a running countdown, an "Initializing…"
 * workflow state, or a "Waiting for events…" stream for any of these.
 */
const TERMINAL_STATUSES: ReadonlySet<SessionResponse["status"]> = new Set([
  "completed",
  "failed",
  "timed_out",
  "stopped",
  "cancelled",
]);

function isTerminalStatus(status: SessionResponse["status"]): boolean {
  return TERMINAL_STATUSES.has(status);
}

function displayStatus(value: string | null | undefined): string {
  return titleCaseIdentifier(value);
}

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
    // List item — render the `*`/`-` marker as a real bullet glyph so raw
    // markdown list stars never show in the bubble.
    const listMatch = lines[i].match(/^(\s*)[*-]\s+(.*)$/);
    if (listMatch) {
      result.push(
        <span key={`ln-${result.length}`} className="block pl-4 -indent-2.5">
          <span aria-hidden="true">• </span>
          {renderInline(listMatch[2])}
        </span>,
      );
      i++;
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

// Drop orphan emphasis markers that survive tokenization — malformed LLM
// markdown (e.g. "**Severity:* Medium", where the bold was opened with `**`
// but closed with a single `*`) otherwise leaks literal `*`/`_` into the
// bubble. Matched, well-formed spans never reach this because they're
// consumed as tokens first.
function stripOrphanMarkers(text: string): string {
  return text.replace(/\*\*|__|(?<=\S)\*(?=\s|$)|(?<=^|\s)\*(?=\S)/g, "");
}

function renderInline(text: string): React.ReactNode[] {
  // Tokenize inline code, bold (**…** / __…__), then italic (*…* / _…_).
  // Bold uses a lazy inner match so a stray single `*` inside doesn't break
  // the pair; italic guards against matching list bullets ("* item") and
  // spaced markers.
  const parts: React.ReactNode[] = [];
  const regex =
    /(`[^`]+`|\*\*.+?\*\*|__.+?__|\*(?!\s)[^*\n]+?\*|_(?!\s)[^_\n]+?_)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  const pushPlain = (raw: string) => {
    const cleaned = stripOrphanMarkers(raw);
    if (cleaned) parts.push(cleaned);
  };

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      pushPlain(text.slice(lastIndex, match.index));
    }
    const m = match[0];
    if (m.startsWith("`")) {
      parts.push(
        <code
          key={`ic-${parts.length}`}
          className="rounded bg-bg-elevated px-1.5 py-0.5 text-[12px] font-mono text-accent-text"
        >
          {m.slice(1, -1)}
        </code>,
      );
    } else if (m.startsWith("**") || m.startsWith("__")) {
      parts.push(
        <strong key={`b-${parts.length}`} className="font-semibold">
          {m.slice(2, -2)}
        </strong>,
      );
    } else {
      parts.push(<em key={`i-${parts.length}`}>{m.slice(1, -1)}</em>);
    }
    lastIndex = match.index + m.length;
  }
  if (lastIndex < text.length) {
    pushPlain(text.slice(lastIndex));
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
        label: workflowNodeLabel(node),
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
    icon: <CircleDot size={14} className="text-accent-text" />,
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
    icon: <Brain size={14} className="text-accent-text" />,
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
  // Intercept (Stop / Override) + Tier 1 redirect steering.
  const [intercepting, setIntercepting] = useState(false);
  const [interceptError, setInterceptError] = useState("");
  const [redirectDrafts, setRedirectDrafts] = useState<Record<string, string>>({});
  // Sprint 58 Step 3: loaded once for ToolCallCard's best-effort
  // tool-name → MCP-server-name lookup. A miss just renders "—".
  const [mcpServers, setMcpServers] = useState<MCPServerResponse[]>([]);

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

  // Initial load — session + incident + chat history. MCP servers fetched
  // alongside for ToolCallCard's best-effort name lookup; failures don't
  // block the page.
  useEffect(() => {
    let cancelled = false;
    listMCPServers()
      .then((res) => {
        if (!cancelled) setMcpServers(res.items);
      })
      .catch(() => {
        if (!cancelled) setMcpServers([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        if (msg.type === "session_overridden") {
          const newTier = msg.data.tier as number | undefined;
          setSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: "active",
                  tier: typeof newTier === "number" ? newTier : prev.tier,
                  ended_at: null,
                }
              : prev,
          );
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

  async function handleRedirect(approvalId: string) {
    const guidance = (redirectDrafts[approvalId] ?? "").trim();
    if (!guidance) return;
    await redirectRequest(approvalId, guidance);
    setPendingApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    setRedirectDrafts((prev) => {
      const next = { ...prev };
      delete next[approvalId];
      return next;
    });
    setEvents((prev) => [
      ...prev,
      {
        id: idGen(),
        kind: "approval",
        label: "Redirected the AI (by you)",
        detail: guidance,
        ts: new Date(),
      },
    ]);
  }

  async function handleStop() {
    if (!id || intercepting) return;
    setInterceptError("");
    setIntercepting(true);
    try {
      const updated = await stopSession(id);
      setSession(updated);
      setPendingApprovals([]);
      setEvents((prev) => [
        ...prev,
        {
          id: idGen(),
          kind: "end",
          label: updated.status === "cancelled"
            ? "Queued session cancelled (by you)"
            : "Session stopped (by you)",
          ts: new Date(),
        },
      ]);
    } catch (err) {
      setInterceptError(err instanceof Error ? err.message : "Failed to stop session");
    } finally {
      setIntercepting(false);
    }
  }

  async function handleOverride(targetTier: number) {
    if (!id || intercepting) return;
    setInterceptError("");
    setIntercepting(true);
    try {
      const updated = await overrideSession(id, { tier: targetTier });
      setSession(updated);
      setPendingApprovals([]);
      setEvents((prev) => [
        ...prev,
        {
          id: idGen(),
          kind: "approval",
          label: `Overridden to Tier ${targetTier} (by you)`,
          ts: new Date(),
        },
      ]);
    } catch (err) {
      setInterceptError(err instanceof Error ? err.message : "Failed to override session");
    } finally {
      setIntercepting(false);
    }
  }

  async function handleSend() {
    const content = draft.trim();
    // Guard: no empty sends, no double-sends, and never post to an ended
    // session (the input is also disabled, this is defense-in-depth).
    if (!content || sending || !id || !canChat) return;
    if (session && isTerminalStatus(session.status)) return;
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

  const chatDisabled = !canChat || (!!session && isTerminalStatus(session.status));

  const inputPlaceholder = useMemo(() => {
    if (!canChat) return "Chat is read-only for viewers.";
    if (session && isTerminalStatus(session.status)) {
      return "This session has ended — chat is read-only.";
    }
    return "Add context or ask anything…";
  }, [canChat, session]);

  const tier0Timer = useMemo(() => {
    // Only a live tier-0 run has a meaningful countdown. Terminal and queued
    // sessions never show it (a terminal session showing "0:00" is the core
    // contradictory-state bug).
    if (
      !session
      || (session.status !== "active" && session.status !== "awaiting_approval")
      || session.tier !== 0
      || !session.tier0_max_session_seconds
    ) {
      return null;
    }
    const startedMs = new Date(session.started_at).getTime();
    const deadlineMs = startedMs + session.tier0_max_session_seconds * 1000;
    const nowMs = Date.now() + timerTick * 0;
    const remainingSeconds = Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
    const expired = remainingSeconds <= 0;
    const label = expired
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

  const isSyntheticTest = incident?.external_source === "opsmender-test";

  // Rollback replays compensating inverses from audit metadata — only
  // meaningful for a tier-0 session that actually reached execution. A
  // still-queued session, or one cancelled out of the queue, never ran any
  // remediation, so there is nothing to roll back.
  const canRollbackSession =
    canRollback
    && session.tier === 0
    && session.status !== "queued"
    && session.status !== "cancelled";

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
                {isSyntheticTest && (
                  <Badge variant="high">TEST · synthetic alert</Badge>
                )}
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
                    {displayStatus(incident.status)}
                  </Badge>
                )}
                <Badge variant={session.status as Parameters<typeof Badge>[0]["variant"]}>
                  {displayStatus(session.status)}
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
                {session.status === "queued" && session.queued_at ? (
                  <span className="ml-2">
                    queued {formatTime(session.queued_at)}
                  </span>
                ) : session.started_at ? (
                  <span className="ml-2">
                    started {formatTime(session.started_at)}
                  </span>
                ) : null}
              </p>
            </div>
            {isTerminalStatus(session.status) ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full shrink-0 bg-bg-elevated text-fg-secondary">
                <span className="h-1.5 w-1.5 rounded-full bg-fg-muted" />
                {session.ended_at
                  ? `Ended · ${formatRelative(session.ended_at)}`
                  : "Ended"}
              </span>
            ) : (
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
            )}
          </div>
          {session.status === "queued" && (
            <div className="mt-3 rounded-lg border border-status-high-border bg-status-high-bg px-3 py-2 text-xs text-status-high">
              Waiting for model capacity
              {session.queue_expires_at
                ? ` until ${formatTime(session.queue_expires_at)}`
                : ""}
              . The model is selected again when this session reaches the front
              of the queue.
            </div>
          )}
          {canChat && (
            session.status === "queued"
            || session.status === "active"
            || session.status === "awaiting_approval"
          ) && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-3">
              <p className="text-xs text-fg-secondary">
                {session.status === "queued"
                  ? "Cancel this queued session before it starts."
                  : "Intercept this running session — stop the AI, or override into a less-autonomous tier and take control."}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={handleStop}
                  loading={intercepting}
                  className="text-status-critical hover:bg-status-critical-bg hover:text-status-critical"
                >
                  <StopCircle size={14} />
                  {session.status === "queued" ? "Cancel queued session" : "Stop"}
                </Button>
                {session.status !== "queued" && session.tier < 1 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleOverride(1)}
                    disabled={intercepting}
                  >
                    <Split size={14} /> Override → Tier 1
                  </Button>
                )}
                {session.status !== "queued" && session.tier < 2 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleOverride(2)}
                    disabled={intercepting}
                  >
                    <Split size={14} /> Override → Tier 2
                  </Button>
                )}
              </div>
            </div>
          )}
          {interceptError && (
            <p className="mt-2 text-xs text-status-critical">{interceptError}</p>
          )}
          {canRollbackSession && (
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

      {/* Sprint 58 Step 1: operator-facing workflow state pipeline. Sits
          between the context strip and the body so it stays visible as
          the agent transitions through states. */}
      <SessionWorkflowState
        sessionStatus={session.status}
        events={events}
        tier={session.tier}
        summary={session.summary}
      />

      {/* Sprint 58 Step 2: tier capability summary. Always-visible
          headline ("what this tier permits") with an expandable matrix
          comparing all three AI Autonomy tiers; collapsed by default to
          keep the page lean. */}
      <TierCapabilitySummary tier={session.tier} defaultCollapsed />

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
              className="flex flex-col gap-3 rounded-lg bg-bg-panel border border-status-medium-border/60 p-4 sm:flex-row sm:items-start sm:gap-4"
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
                  Expires {formatTime(a.expires_at)}
                </p>
                <div className="mt-2.5">
                  <Label htmlFor={`redirect-${a.id}`} className="text-[11px]">
                    Redirect (steer the AI instead of approving)
                  </Label>
                  <textarea
                    id={`redirect-${a.id}`}
                    value={redirectDrafts[a.id] ?? ""}
                    onChange={(e) =>
                      setRedirectDrafts((prev) => ({ ...prev, [a.id]: e.target.value }))
                    }
                    placeholder="e.g. drain the node first, then restart the pod"
                    rows={2}
                    className="mt-1 w-full resize-none rounded-lg border border-border-subtle bg-bg-input px-3 py-2 text-xs shadow-sm placeholder:text-fg-muted focus:border-accent focus:ring-1 focus:ring-accent transition-colors"
                  />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:flex sm:flex-col sm:shrink-0">
                <Button
                  size="sm"
                  variant="success"
                  onClick={() => handleApprove(a.id)}
                  className="justify-center sm:min-w-[100px]"
                >
                  <CheckCircle2 size={14} />
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleReject(a.id)}
                  className="justify-center text-status-critical hover:bg-status-critical-bg hover:text-status-critical sm:min-w-[100px]"
                >
                  <XCircle size={14} />
                  Reject
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => handleRedirect(a.id)}
                  disabled={!(redirectDrafts[a.id] ?? "").trim()}
                  className="justify-center sm:min-w-[100px]"
                >
                  <CornerUpRight size={14} />
                  Redirect
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Split view: event stream + co-pilot chat. On mobile the two panels
          stack as full-width blocks with their own bounded height (below), in
          normal page flow — the grid only fills remaining height at lg+, where
          the panels scroll internally instead of the page. */}
      <div className="grid grid-cols-1 gap-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[3fr_2fr]">
        {/* Event stream */}
        <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm max-h-[70vh] lg:max-h-none lg:min-h-0">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
            <Terminal size={14} className="text-fg-muted" />
            <span className="text-xs font-medium text-fg-secondary uppercase tracking-wide">
              Event Stream
            </span>
            <span className="ml-auto text-[10px] text-fg-muted tabular-nums font-mono">
              {events.length} events
            </span>
          </div>

          <div
            className="flex-1 overflow-y-auto"
            tabIndex={0}
            role="log"
            aria-label="Session event stream"
            aria-live="polite"
            aria-relevant="additions text"
          >
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-fg-muted">
                <CircleDot
                  size={24}
                  className={`mb-2 ${isTerminalStatus(session.status) ? "" : "animate-pulse"}`}
                />
                <p className="text-sm">
                  {isTerminalStatus(session.status)
                    ? "No events were recorded for this session."
                    : "Waiting for events…"}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-border-subtle/50">
                {events.map((ev) => {
                  // Sprint 58 Step 3+4: tool events get a richer card
                  // with phase pill, MCP-server lookup, parameters /
                  // result disclosure, and a blocked-action callout
                  // when the tier gate refused the action.
                  if (ev.kind === "tool") {
                    return (
                      <ToolCallCard
                        key={ev.id}
                        raw={ev.raw as Parameters<typeof ToolCallCard>[0]["raw"]}
                        fallbackName={ev.label}
                        mcpServers={mcpServers}
                        ts={ev.ts}
                        durationMs={ev.durationMs}
                      />
                    );
                  }
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
                            {formatTime(ev.ts)}
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
        <div className="flex flex-col rounded-xl border border-border-subtle bg-bg-panel shadow-sm max-h-[70vh] lg:max-h-none lg:min-h-0">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
            <MessageSquare size={14} className="text-accent-text" />
            <span className="text-xs font-medium text-fg-secondary uppercase tracking-wide">
              Co-pilot
            </span>
            <span className="ml-auto text-[10px] text-fg-muted tabular-nums font-mono">
              {messages.length} messages
            </span>
          </div>

          <div
            className="flex-1 overflow-y-auto px-3 py-3 space-y-3 bg-bg-elevated/30"
            tabIndex={0}
            role="region"
            aria-label="Session co-pilot chat"
          >
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
                aria-label="Message the co-pilot"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={chatDisabled}
                placeholder={inputPlaceholder}
                rows={2}
                className="flex-1 resize-none rounded-lg border border-border-subtle bg-bg-input px-3 py-2 text-sm shadow-sm placeholder:text-fg-muted focus:border-accent focus:ring-1 focus:ring-accent disabled:bg-bg-elevated disabled:text-fg-muted transition-colors"
              />
              <Button
                size="sm"
                aria-label="Send message"
                title="Send message"
                onClick={handleSend}
                loading={sending}
                disabled={chatDisabled || !draft.trim()}
              >
                <Send size={13} />
              </Button>
            </div>
          </div>
        </div>
      </div>

      <SessionMemoriesPanel sessionId={session.id} />

      {canRollbackSession && (
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
                      {displayStatus(step.status)}
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
            ? "bg-accent text-accent-contrast rounded-br-sm"
            : "bg-bg-panel text-fg-primary border border-border-subtle rounded-bl-sm"
        }`}
      >
        <div className="leading-relaxed">
          {isUser ? message.content : renderMarkdown(message.content)}
        </div>
        <div
          className={`mt-1.5 flex items-center gap-1.5 text-[10px] ${
            isUser ? "text-accent-contrast" : "text-fg-muted"
          }`}
        >
          <span className="tabular-nums font-mono">{formatTime(ts)}</span>
          {message.node_context && (
            <span className={isUser ? "" : "opacity-75"}>
              · {message.node_context}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
