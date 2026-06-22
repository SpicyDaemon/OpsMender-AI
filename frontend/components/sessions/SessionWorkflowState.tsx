"use client";

/**
 * Sprint 58 (UX direction "Sprint B") Step 1 — Session workflow-state header.
 *
 * Surfaces the current LangGraph workflow stage in operator-friendly
 * language so the AI never feels like an opaque chat box. The eight
 * pipeline states are derived from the LangGraph node names in
 * `backend/agent/graph.py` (`recall`, `observe`, `diagnose`, `plan`,
 * `tier_gate`, `execute`, `verify`, `summarize`, `remember`) plus the
 * session-level `awaiting_approval` status. `recall` and `remember` are
 * folded into Observing and Summarizing respectively — operators don't
 * need to see those as primary states. `tier_gate` is not a primary
 * state either; it shows as a brief sub-state badge when active.
 *
 * Input: the LogEvent list the session detail page already maintains
 * (most-recent-first) + the current session status. The component
 * computes:
 *   - which states are completed (checkmark)
 *   - which is current (highlighted)
 *   - which are still ahead (muted)
 *
 * No new backend dependency. No new fetches. Pure presentation on top
 * of state the session detail page already tracks.
 */

import {
  CheckCircle2,
  Circle,
  Eye,
  FileText,
  GitBranch,
  Hourglass,
  Search,
  Shield,
  Sparkles,
  Wrench,
} from "lucide-react";

// -- Public types --------------------------------------------------------

/** Operator-facing workflow state, in pipeline order. */
export type WorkflowStateKey =
  | "observe"
  | "diagnose"
  | "plan"
  | "awaiting_approval"
  | "execute"
  | "verify"
  | "summarize"
  | "completed";

export type WorkflowStateStatus = "done" | "current" | "pending" | "skipped";

interface PipelineDef {
  key: WorkflowStateKey;
  label: string;
  icon: typeof Eye;
}

const PIPELINE: PipelineDef[] = [
  { key: "observe", label: "Observing", icon: Eye },
  { key: "diagnose", label: "Diagnosing", icon: Search },
  { key: "plan", label: "Planning", icon: GitBranch },
  { key: "awaiting_approval", label: "Waiting for approval", icon: Hourglass },
  { key: "execute", label: "Executing", icon: Wrench },
  { key: "verify", label: "Verifying", icon: CheckCircle2 },
  { key: "summarize", label: "Summarizing", icon: FileText },
  { key: "completed", label: "Done", icon: Sparkles },
];

// -- Inputs the host page already maintains ------------------------------

/**
 * Minimal contract used by the session detail page. We accept just
 * the fields we need so this component can be unit-tested without a
 * full session/LogEvent fixture.
 */
export interface WorkflowStateInputs {
  /** Session.status as returned by GET /sessions/{id}. */
  sessionStatus:
    | "queued"
    | "active"
    | "awaiting_approval"
    | "completed"
    | "failed"
    | "timed_out"
    | "stopped"
    | "cancelled";
  /**
   * The oldest-first event log (the host page appends WebSocket
   * messages, so `events[0]` is the earliest and `events.at(-1)` is
   * most recent). Only `kind === "node" | "tier_gate"` events are
   * inspected; tool / approval / error events are ignored here
   * because they don't represent a workflow transition.
   */
  events: Array<{ kind: string; label: string }>;
}

// -- Derivation logic ----------------------------------------------------

const NODE_TO_STATE: Record<string, WorkflowStateKey> = {
  // Folds: recall → observe (the memory-pull belongs to context-gathering)
  recall: "observe",
  observe: "observe",
  diagnose: "diagnose",
  plan: "plan",
  // tier_gate is intentionally not in this map — it's a sub-state, not a
  // primary stage. The host page already styles tier_gate events.
  execute: "execute",
  verify: "verify",
  summarize: "summarize",
  // Fold: remember → summarize (memory writeback is the tail of summarizing)
  remember: "summarize",
};

/** Lowercased node name extracted from a "node" event label. */
function nodeFromEvent(ev: { kind: string; label: string }): string | null {
  if (ev.kind !== "node") return null;
  return ev.label.toLowerCase().replace(/\s+/g, "_");
}

/** Compute current stage + per-state status for the pipeline. */
export function deriveStates(
  inputs: WorkflowStateInputs,
): Array<PipelineDef & { status: WorkflowStateStatus }> {
  const seen = new Set<WorkflowStateKey>();

  // events are oldest-first; walk in order so `seen` reflects pipeline
  // progression.
  for (const ev of inputs.events) {
    const node = nodeFromEvent(ev);
    if (!node) continue;
    const mapped = NODE_TO_STATE[node];
    if (mapped) seen.add(mapped);
  }

  // Determine the current state.
  let current: WorkflowStateKey | null = null;
  if (inputs.sessionStatus === "completed") {
    current = "completed";
  } else if (inputs.sessionStatus === "awaiting_approval") {
    current = "awaiting_approval";
  } else if (inputs.sessionStatus === "failed" || inputs.sessionStatus === "timed_out") {
    // Failed/timed out: highlight the most recent node, no "Done" tick.
    for (let i = inputs.events.length - 1; i >= 0; i--) {
      const n = nodeFromEvent(inputs.events[i]);
      const m = n ? NODE_TO_STATE[n] : null;
      if (m) {
        current = m;
        break;
      }
    }
  } else {
    // sessionStatus === "active" — the most recent node is current.
    for (let i = inputs.events.length - 1; i >= 0; i--) {
      const node = nodeFromEvent(inputs.events[i]);
      const mapped = node ? NODE_TO_STATE[node] : null;
      if (mapped) {
        current = mapped;
        break;
      }
    }
    if (!current && seen.has("summarize")) current = "summarize";
  }

  return PIPELINE.map((def) => {
    if (def.key === current) return { ...def, status: "current" as const };
    if (
      current &&
      PIPELINE.findIndex((d) => d.key === def.key) <
        PIPELINE.findIndex((d) => d.key === current)
    ) {
      // Earlier in the pipeline than the current state.
      // Awaiting-approval is a special sub-state: it doesn't render as
      // "done" once we're past it, just "skipped".
      if (def.key === "awaiting_approval" && !seen.has("awaiting_approval")) {
        return { ...def, status: "skipped" as const };
      }
      return { ...def, status: "done" as const };
    }
    if (current === "completed") return { ...def, status: "done" as const };
    return { ...def, status: "pending" as const };
  });
}

// -- Component -----------------------------------------------------------

interface Props {
  /** Session.status. Same shape as `SessionResponse.status`. */
  sessionStatus: WorkflowStateInputs["sessionStatus"];
  /** Most-recent-first LogEvent list from the host page. */
  events: WorkflowStateInputs["events"];
  /** Optional Tier number for the small badge on the right. */
  tier?: number | null;
}

export function SessionWorkflowState({ sessionStatus, events, tier }: Props) {
  const states = deriveStates({ sessionStatus, events });
  const current = states.find((s) => s.status === "current") ?? null;

  // tier_gate visual nudge: when the most recent node event is tier_gate
  // we surface a small inline indicator over the Execute pill so operators
  // see that the permission check is in flight.
  const tierGateActive = events.at(-1)?.kind === "tier_gate";

  return (
    <div
      data-testid="session-workflow-state"
      className="mb-4 rounded-xl border border-border-subtle bg-bg-panel px-4 py-3 shadow-sm sm:px-5 sm:py-4"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
            Workflow state
          </p>
          <p className="mt-0.5 truncate text-sm font-semibold text-fg-primary">
            {current ? current.label : "Initializing…"}
          </p>
        </div>
        {tier !== null && tier !== undefined && (
          <span
            className="inline-flex items-center gap-1.5 rounded-pill border border-border-subtle bg-bg-elevated px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-fg-secondary"
            title={`Tier ${tier}`}
          >
            <Shield size={10} className="text-fg-muted" />
            Tier {tier}
          </span>
        )}
      </div>

      {/* Pipeline strip — horizontal on lg+, wraps on smaller. */}
      <ol
        className="flex flex-wrap items-center gap-2 sm:gap-3"
        aria-label="Workflow pipeline"
      >
        {states.map((s, idx) => {
          const Icon = s.icon;
          const isLast = idx === states.length - 1;
          return (
            <li key={s.key} className="flex items-center gap-2 sm:gap-3">
              <span
                className={[
                  "inline-flex items-center gap-1.5 rounded-pill border px-2 py-1 text-[11px] font-medium",
                  s.status === "done"
                    ? "border-status-low-border bg-status-low-bg text-status-low"
                    : s.status === "current"
                      ? "border-accent bg-accent-bg/40 text-fg-primary shadow-sm"
                      : s.status === "skipped"
                        ? "border-border-subtle bg-transparent text-fg-muted line-through"
                        : "border-border-subtle bg-bg-elevated text-fg-muted",
                ].join(" ")}
                title={s.label}
              >
                {s.status === "done" ? (
                  <CheckCircle2 size={11} />
                ) : s.status === "current" ? (
                  <Icon size={11} className="animate-pulse" />
                ) : (
                  <Circle size={11} />
                )}
                {s.label}
                {s.key === "execute" && tierGateActive && (
                  <span
                    className="ml-1 inline-flex items-center gap-0.5 rounded-pill bg-status-medium-bg px-1 py-px text-[9px] font-semibold uppercase tracking-wide text-status-medium"
                    title="Tier gate evaluating this step"
                  >
                    Tier check
                  </span>
                )}
              </span>
              {!isLast && (
                <span
                  aria-hidden="true"
                  className={
                    s.status === "done"
                      ? "h-px w-3 bg-status-low/60 sm:w-5"
                      : "h-px w-3 bg-border-subtle sm:w-5"
                  }
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
