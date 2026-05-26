"use client";

/**
 * Sprint 58 Steps 3+4 — Tool call card with governed-AI surface.
 *
 * Replaces the generic event row for `kind === "tool"` events on
 * /dashboard/sessions/detail. Each tool event renders as a richer
 * structured card that exposes everything an operator needs to audit
 * the action without leaving the page:
 *
 *   - Tool name
 *   - MCP server (best-effort lookup; falls back to "—")
 *   - Phase pill: Executing / Completed / Blocked
 *   - Parameters block (collapsible if large)
 *   - Result preview (only when phase = end)
 *   - Runtime (when known)
 *   - Block-reason callout (only when phase = blocked) -- this is
 *     Step 4 of the sprint, surfaced inline rather than as a tooltip
 *
 * Safety-class classification (safe / caution / destructive) is not
 * surfaced yet because it lives in the MCP server's SKILL.md
 * markdown body rather than in a structured field. Adding it would
 * require a backend schema change; tracked as a sprint follow-up.
 */

import { useState } from "react";
import {
  AlertOctagon,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Server,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import type { MCPServerResponse } from "@/lib/types";

// -- Input shape --------------------------------------------------------

interface ToolEventRaw {
  tool_name?: string;
  parameters?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  permitted?: boolean;
  phase?: "start" | "end" | "blocked";
  block_reason?: string | null;
  duration_ms?: number;
  /** MCP server name if the runner happens to expose it. Today the
   *  WSMessage doesn't carry this; we look it up best-effort instead. */
  mcp_server_name?: string;
}

interface Props {
  /** The LogEvent.raw payload for a `kind === "tool"` event. */
  raw: ToolEventRaw | undefined;
  /** Fallback tool name when raw is undefined. */
  fallbackName?: string;
  /** Loaded MCP server list for best-effort name lookup. */
  mcpServers?: MCPServerResponse[];
  /** Event timestamp for the row header. */
  ts: Date;
  /** Runtime in ms if known. */
  durationMs?: number;
}

// -- Helpers ------------------------------------------------------------

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/** Heuristic match: the WS payload doesn't carry the MCP server id, so
 *  we try to match the tool name's common prefix (e.g. "kubectl_*")
 *  against the loaded MCP server names. Best-effort only -- a miss
 *  just renders "—" in the chip. */
function guessMCPServerName(
  toolName: string | undefined,
  servers: MCPServerResponse[] | undefined,
): string | null {
  if (!toolName || !servers || servers.length === 0) return null;
  const lower = toolName.toLowerCase();
  // 1. Direct token match on server name.
  for (const s of servers) {
    const sn = s.name.toLowerCase();
    if (lower.startsWith(sn) || lower.includes(sn)) return s.name;
  }
  // 2. Prefix-before-underscore match.
  const prefix = lower.split("_")[0];
  for (const s of servers) {
    if (s.name.toLowerCase().includes(prefix)) return s.name;
  }
  return null;
}

// -- Component ----------------------------------------------------------

export function ToolCallCard({
  raw,
  fallbackName,
  mcpServers,
  ts,
  durationMs,
}: Props) {
  const [paramsOpen, setParamsOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);

  const toolName = raw?.tool_name ?? fallbackName ?? "unknown";
  const phase = raw?.phase ?? "start";
  const permitted = raw?.permitted ?? true;
  const blockReason = raw?.block_reason ?? null;
  const params = raw?.parameters ?? null;
  const result = raw?.result ?? null;
  const mcpServer =
    raw?.mcp_server_name ?? guessMCPServerName(toolName, mcpServers);
  const runtime = durationMs ?? raw?.duration_ms ?? null;

  // -- Visual treatments per phase -----------------------------------

  let borderClass = "border-border-strong";
  let bgClass = "";
  let phasePill: React.ReactNode = null;
  let icon: React.ReactNode = <Wrench size={14} className="text-fg-secondary" />;

  if (phase === "blocked" || permitted === false) {
    borderClass = "border-status-critical/60";
    bgClass = "bg-status-critical-bg/20";
    icon = <ShieldAlert size={14} className="text-status-critical" />;
    phasePill = (
      <span className="inline-flex items-center gap-1 rounded-pill border border-status-critical-border bg-status-critical-bg px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-critical">
        <AlertOctagon size={10} />
        Blocked
      </span>
    );
  } else if (phase === "end") {
    borderClass = "border-status-low/40";
    bgClass = "bg-status-low-bg/15";
    icon = <CheckCircle2 size={14} className="text-status-low" />;
    phasePill = (
      <span className="inline-flex items-center gap-1 rounded-pill border border-status-low-border bg-status-low-bg px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-low">
        Completed
      </span>
    );
  } else {
    // phase === "start" — in flight
    icon = <Loader2 size={14} className="animate-spin text-accent" />;
    phasePill = (
      <span className="inline-flex items-center gap-1 rounded-pill border border-accent/40 bg-accent-bg/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
        Executing
      </span>
    );
  }

  const paramsString = params ? JSON.stringify(params, null, 2) : null;
  const resultString = result ? JSON.stringify(result, null, 2) : null;

  return (
    <div
      data-testid="tool-call-card"
      data-phase={phase}
      className={`flex gap-3 border-l-2 px-4 py-3 transition-colors hover:bg-bg-hover/40 ${borderClass} ${bgClass}`}
    >
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        {/* Header row: tool name + phase + mcp server + runtime + ts */}
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-sm font-medium text-fg-primary">
              {toolName}
            </p>
            {phasePill}
            {mcpServer && (
              <span
                className="inline-flex items-center gap-1 rounded-pill border border-border-subtle bg-bg-elevated px-1.5 py-0.5 text-[10px] text-fg-secondary"
                title="MCP server"
              >
                <Server size={9} />
                {mcpServer}
              </span>
            )}
            {runtime != null && (
              <span className="rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-fg-muted">
                {formatMs(runtime)}
              </span>
            )}
          </div>
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-fg-muted">
            {ts.toLocaleTimeString()}
          </span>
        </div>

        {/* Blocked-action callout (Step 4). When phase=blocked, the
            tier/skill rule that refused the action gets prominent
            treatment instead of being buried in the parameters blob. */}
        {(phase === "blocked" || permitted === false) && blockReason && (
          <div
            className="mt-2 rounded-lg border border-status-critical-border bg-status-critical-bg/40 p-3"
            data-testid="blocked-action-callout"
          >
            <div className="mb-1 flex items-center gap-1.5">
              <ShieldAlert size={12} className="text-status-critical" />
              <p className="text-[10px] font-semibold uppercase tracking-wide text-status-critical">
                Blocked by tier gate
              </p>
            </div>
            <p className="text-xs leading-5 text-fg-primary">{blockReason}</p>
          </div>
        )}

        {/* Parameters (collapsed by default; show "View parameters"
            toggle so the page doesn't get visually heavy). */}
        {paramsString && paramsString !== "{}" && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setParamsOpen((o) => !o)}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-fg-secondary hover:text-fg-primary"
            >
              <ChevronRight
                size={11}
                className={`transition-transform ${paramsOpen ? "rotate-90" : ""}`}
              />
              {paramsOpen ? "Hide" : "View"} parameters
            </button>
            {paramsOpen && (
              <pre className="mt-1 overflow-x-auto rounded bg-bg-elevated/60 p-2 font-mono text-[11px] leading-relaxed text-fg-secondary">
                {paramsString}
              </pre>
            )}
          </div>
        )}

        {/* Result preview (only on phase=end with a non-empty result). */}
        {phase === "end" && resultString && resultString !== "{}" && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setResultOpen((o) => !o)}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-fg-secondary hover:text-fg-primary"
            >
              <ChevronRight
                size={11}
                className={`transition-transform ${resultOpen ? "rotate-90" : ""}`}
              />
              {resultOpen ? "Hide" : "View"} result
            </button>
            {resultOpen && (
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-bg-elevated/60 p-2 font-mono text-[11px] leading-relaxed text-fg-secondary">
                {resultString}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
