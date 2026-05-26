"use client";

/**
 * Sprint 58 Step 2 — Tier capability summary.
 *
 * The UX direction doc says: "Always show what the AI is allowed to do
 * and what it isn't." This is the smallest surface that satisfies that
 * commitment — a compact two-column card with what each tier permits
 * and what it doesn't. The current session's tier is highlighted
 * inline; the other tiers are visible but muted so operators can
 * always orient themselves against the full ladder.
 *
 * Sits next to / below the workflow-state pipeline on
 * /dashboard/sessions/detail. Pure presentation; no fetches.
 */

import { useState } from "react";
import {
  Check,
  ChevronDown,
  Shield,
  X,
} from "lucide-react";

interface TierDef {
  tier: 0 | 1 | 2 | 3;
  headline: string;
  oneLiner: string;
  allowed: string[];
  notAllowed: string[];
  tone: "critical" | "high" | "medium" | "low";
}

const TIERS: TierDef[] = [
  {
    tier: 0,
    headline: "Autonomous",
    oneLiner: "Full autonomous execution. Sandbox / non-prod only. Hard time limits.",
    allowed: [
      "Safe operations",
      "Caution operations",
      "Destructive operations",
    ],
    notAllowed: [
      "Anything beyond the configured time budget",
      "Operations outside sandbox / non-prod environments",
    ],
    tone: "critical",
  },
  {
    tier: 1,
    headline: "Approval",
    oneLiner: "AI proposes + executes after a human approves the specific action.",
    allowed: [
      "Safe operations (after approval)",
      "Caution operations (after approval)",
      "Destructive operations (after approval)",
    ],
    notAllowed: ["Anything without an approved request"],
    tone: "high",
  },
  {
    tier: 2,
    headline: "Assisted",
    oneLiner: "AI executes safe + caution operations autonomously. Destructive requires Tier 1 approval.",
    allowed: [
      "Safe operations (autonomous)",
      "Caution operations (autonomous)",
    ],
    notAllowed: [
      "Destructive operations (escalate to Tier 1)",
    ],
    tone: "medium",
  },
  {
    tier: 3,
    headline: "Advisory",
    oneLiner: "AI advises only. Humans execute every operation.",
    allowed: [
      "Read-only context gathering",
      "Diagnosis + planning + recommendations",
    ],
    notAllowed: [
      "Any tool execution — the human performs every action manually",
    ],
    tone: "low",
  },
];

const TONE_CLASSES: Record<TierDef["tone"], string> = {
  critical:
    "border-status-critical-border bg-status-critical-bg/30 text-status-critical",
  high: "border-status-high-border bg-status-high-bg/30 text-status-high",
  medium:
    "border-status-medium-border bg-status-medium-bg/30 text-status-medium",
  low: "border-status-low-border bg-status-low-bg/30 text-status-low",
};

interface Props {
  /** Active session's tier. */
  tier: number;
  /** When true, mounts collapsed by default. */
  defaultCollapsed?: boolean;
}

export function TierCapabilitySummary({ tier, defaultCollapsed = false }: Props) {
  const [expanded, setExpanded] = useState(!defaultCollapsed);
  const current = TIERS.find((t) => t.tier === tier) ?? null;

  return (
    <div
      data-testid="tier-capability-summary"
      className="mb-4 rounded-xl border border-border-subtle bg-bg-panel shadow-sm"
    >
      {/* Headline row — always visible. */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 px-4 py-3 text-left sm:px-5 sm:py-4"
      >
        <Shield size={18} className="shrink-0 text-fg-secondary" />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">
            What this tier permits
          </p>
          <p className="mt-0.5 truncate text-sm font-semibold text-fg-primary">
            {current
              ? `Tier ${current.tier} · ${current.headline} — ${current.oneLiner}`
              : `Tier ${tier} (unknown)`}
          </p>
        </div>
        <ChevronDown
          size={16}
          className={`shrink-0 text-fg-muted transition-transform ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Expanded body — full capability matrix. */}
      {expanded && (
        <div className="border-t border-border-subtle px-4 py-4 sm:px-5 sm:py-5">
          <div className="grid gap-3 sm:grid-cols-2">
            {TIERS.map((t) => {
              const isCurrent = t.tier === tier;
              return (
                <div
                  key={t.tier}
                  className={[
                    "rounded-lg border px-4 py-3 transition-colors",
                    isCurrent
                      ? TONE_CLASSES[t.tone]
                      : "border-border-subtle bg-bg-elevated/60",
                  ].join(" ")}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className={[
                        "inline-flex h-6 min-w-6 items-center justify-center rounded-md border px-1.5 font-mono text-[11px] font-semibold",
                        isCurrent
                          ? "border-current"
                          : "border-border-subtle text-fg-secondary",
                      ].join(" ")}
                    >
                      T{t.tier}
                    </span>
                    <span
                      className={`text-sm font-semibold ${
                        isCurrent ? "" : "text-fg-primary"
                      }`}
                    >
                      {t.headline}
                    </span>
                    {isCurrent && (
                      <span className="ml-auto rounded-pill border border-current px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide">
                        Active
                      </span>
                    )}
                  </div>
                  <p
                    className={`mb-3 text-xs leading-5 ${
                      isCurrent ? "" : "text-fg-secondary"
                    }`}
                  >
                    {t.oneLiner}
                  </p>
                  <ul
                    className={`space-y-1 text-xs leading-5 ${
                      isCurrent ? "" : "text-fg-secondary"
                    }`}
                  >
                    {t.allowed.map((line) => (
                      <li key={`a-${line}`} className="flex items-start gap-1.5">
                        <Check
                          size={12}
                          className="mt-0.5 shrink-0 text-status-low"
                        />
                        <span>{line}</span>
                      </li>
                    ))}
                    {t.notAllowed.map((line) => (
                      <li key={`n-${line}`} className="flex items-start gap-1.5">
                        <X
                          size={12}
                          className="mt-0.5 shrink-0 text-status-critical"
                        />
                        <span>{line}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
          <p className="mt-4 text-[11px] text-fg-muted">
            Tier is set per environment in <code className="font-mono">Config → Runtime defaults</code>. Tools are
            classified <em>safe</em> / <em>caution</em> / <em>destructive</em> by each MCP server&apos;s SKILL.md.
            The tier gate is enforced programmatically — the agent cannot reason past it.
          </p>
        </div>
      )}
    </div>
  );
}
