"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ChevronRight, X as XIcon } from "lucide-react";
import { getSetupChecklist } from "@/lib/api";
import type { SetupChecklistResponse } from "@/lib/types";
import { useDashboardHref } from "@/lib/use-dashboard-href";

type Row = {
  key: keyof Omit<SetupChecklistResponse, "all_complete">;
  label: string;
  href: string;
  hint: string;
};

const ROWS: Row[] = [
  {
    key: "model_configured",
    label: "Configure an AI model",
    href: "/dashboard/models",
    hint: "OpsMender needs at least one LLM provider key to drive sessions.",
  },
  {
    key: "mcp_server_added",
    label: "Add an MCP server",
    href: "/dashboard/mcp-servers",
    hint: "MCP servers are how OpsMender reaches your infrastructure.",
  },
  {
    key: "skill_defined",
    label: "Define a skill",
    href: "/dashboard/skills",
    hint: "SKILL.md tells the AI which operations are safe / caution / destructive.",
  },
  {
    key: "ingest_token_created",
    label: "Set up alert intake",
    href: "/dashboard/paging/services",
    hint: "Services are where inbound alerts should land and route.",
  },
  {
    key: "paging_service_added",
    label: "Add a paging service (optional)",
    href: "/dashboard/paging/services",
    hint: "Services own incidents and route them through escalation chains.",
  },
];

export function SetupChecklist() {
  const [state, setState] = useState<SetupChecklistResponse | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const dashboardHref = useDashboardHref();

  useEffect(() => {
    if (typeof window !== "undefined") {
      setDismissed(window.localStorage.getItem("opsmender:setup-checklist-dismissed") === "1");
    }
    let cancelled = false;
    void (async () => {
      try {
        const data = await getSetupChecklist();
        if (!cancelled) setState(data);
      } catch {
        // Silently skip — checklist is best-effort.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === null || state.all_complete || dismissed) {
    return null;
  }

  const completedCount = ROWS.filter((row) => state[row.key]).length;

  const handleDismiss = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("opsmender:setup-checklist-dismissed", "1");
    }
    setDismissed(true);
  };

  return (
    <div className="mb-6 rounded-card border border-status-info-border bg-status-info-bg/30 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-fg-primary">
            Finish setting up OpsMender
          </h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            {completedCount} of {ROWS.length} steps complete. Each link drops you on the
            right setup page.
          </p>
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          className="rounded p-1 text-fg-muted hover:text-fg-primary"
          aria-label="Dismiss setup checklist"
        >
          <XIcon size={16} />
        </button>
      </div>
      <ul className="mt-3 flex flex-col gap-1.5">
        {ROWS.map((row) => {
          const done = state[row.key];
          return (
            <li key={row.key}>
              <Link
                href={dashboardHref(row.href)}
                className={`flex items-center gap-3 rounded px-2 py-1.5 text-sm transition-colors hover:bg-bg-hover ${
                  done ? "text-fg-muted" : "text-fg-primary"
                }`}
              >
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full border ${
                    done
                      ? "border-status-low-border bg-status-low-bg text-status-low"
                      : "border-border-default"
                  }`}
                  aria-hidden
                >
                  {done ? <Check size={12} /> : null}
                </span>
                <span className={done ? "line-through" : ""}>{row.label}</span>
                <span className="ml-auto flex items-center gap-1 text-xs text-fg-muted">
                  <span className="hidden sm:inline">{row.hint}</span>
                  <ChevronRight size={14} aria-hidden />
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
