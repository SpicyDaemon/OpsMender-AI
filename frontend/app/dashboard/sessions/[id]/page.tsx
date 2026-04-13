"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  CircleDot,
  Clock,
  Terminal,
  XCircle,
} from "lucide-react";
import { approveRequest, connectSessionStream, getSession, listApprovals } from "@/lib/api";
import type { ApprovalRequestResponse, SessionResponse, WSMessage } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageSpinner } from "@/components/ui/Spinner";

// ---------------------------------------------------------------------------
// Types for the event log
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

function parseWSMessage(msg: WSMessage, idGen: () => number): LogEvent {
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
        detail: msg.data.permitted === false
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
        detail: String(msg.data.message ?? msg.data.error ?? JSON.stringify(msg.data)),
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
  node: {
    icon: <CircleDot size={14} className="text-indigo-500" />,
    line: "border-indigo-100",
  },
  tool: {
    icon: <Terminal size={14} className="text-gray-500" />,
    line: "border-gray-200",
  },
  approval: {
    icon: <Clock size={14} className="text-yellow-500" />,
    line: "border-yellow-100",
  },
  error: {
    icon: <XCircle size={14} className="text-red-500" />,
    line: "border-red-100",
  },
  end: {
    icon: <CheckCircle2 size={14} className="text-green-500" />,
    line: "border-green-100",
  },
  llm: {
    icon: <CircleDot size={14} className="text-purple-500" />,
    line: "border-purple-100",
  },
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequestResponse[]>([]);
  const counterRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const idGen = useCallback(() => ++counterRef.current, []);

  const addEvent = useCallback((ev: LogEvent) => {
    setEvents((prev) => [...prev, ev]);
  }, []);

  const refreshApprovals = useCallback(async () => {
    try {
      const res = await listApprovals({ status: "pending", limit: 50 });
      setPendingApprovals(res.items.filter((a) => a.session_id === id));
    } catch {
      // ignore
    }
  }, [id]);

  // Load session metadata
  useEffect(() => {
    getSession(id)
      .then(setSession)
      .finally(() => setLoading(false));
  }, [id]);

  // Connect WebSocket
  useEffect(() => {
    const ws = connectSessionStream(
      id,
      (msg) => {
        const ev = parseWSMessage(msg, idGen);
        addEvent(ev);
        if (msg.type === "approval_requested") {
          refreshApprovals();
        }
      },
      () => setConnected(false),
    );

    ws.onopen = () => setConnected(true);
    wsRef.current = ws;

    return () => ws.close();
  }, [id, idGen, addEvent, refreshApprovals]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  // Initial approval load
  useEffect(() => { refreshApprovals(); }, [refreshApprovals]);

  async function handleApprove(approvalId: string) {
    await approveRequest(approvalId);
    setPendingApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    addEvent({
      id: idGen(),
      kind: "approval",
      label: "Approval approved (by you)",
      ts: new Date(),
    });
  }

  async function handleReject(approvalId: string) {
    await rejectRequest(approvalId);
    setPendingApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    addEvent({
      id: idGen(),
      kind: "approval",
      label: "Approval rejected (by you)",
      ts: new Date(),
    });
  }

  if (loading) return <PageSpinner />;
  if (!session) return <p className="text-red-600">Session not found.</p>;

  return (
    <div className="max-w-4xl mx-auto flex flex-col gap-4 h-full">
      {/* Back + header */}
      <div>
        <Link
          href="/dashboard/incidents"
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900 mb-3"
        >
          <ArrowLeft size={14} /> Incidents
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              Session <span className="font-mono text-base text-gray-500">{session.id.slice(0, 8)}…</span>
            </h1>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant={session.status as Parameters<typeof Badge>[0]["variant"]}>
                {session.status}
              </Badge>
              <span className="text-xs text-gray-400">Tier {session.tier}</span>
              {session.model_provider && (
                <span className="text-xs text-gray-400">{session.model_provider}/{session.model_id}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${
                connected
                  ? "bg-green-50 text-green-700"
                  : "bg-gray-100 text-gray-500"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-green-500 animate-pulse" : "bg-gray-400"}`}
              />
              {connected ? "Live" : "Disconnected"}
            </span>
          </div>
        </div>
      </div>

      {/* Pending approvals */}
      {pendingApprovals.length > 0 && (
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 space-y-2">
          <p className="text-sm font-semibold text-yellow-800">
            {pendingApprovals.length} pending approval{pendingApprovals.length > 1 ? "s" : ""}
          </p>
          {pendingApprovals.map((a) => (
            <div key={a.id} className="flex items-start gap-3 rounded-lg bg-white border border-yellow-200 p-3">
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

      {/* Event log */}
      <div className="flex-1 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
          <Terminal size={14} className="text-gray-400" />
          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Event Stream
          </span>
        </div>

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
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Summary */}
      {session.summary && (
        <div className="rounded-xl border border-green-200 bg-green-50 p-4">
          <p className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-1">Summary</p>
          <p className="text-sm text-green-800">{session.summary}</p>
        </div>
      )}
    </div>
  );
}

// Import missing rejectRequest
import { rejectRequest } from "@/lib/api";
