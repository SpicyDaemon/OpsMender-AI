"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, ChevronRight, X as XIcon } from "lucide-react";
import { getSetupChecklist } from "@/lib/api";
import { useLiveEvents } from "@/context/liveEvents";
import type { SetupChecklistResponse } from "@/lib/types";

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

const LIVE_REFRESH_CATEGORIES = [
  "account",
  "approval",
  "incident",
  "mention",
  "reliability",
  "session",
];

export function SetupChecklist() {
  const [state, setState] = useState<SetupChecklistResponse | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const mountedRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getSetupChecklist();
      if (mountedRef.current) setState(data);
    } catch {
      if (mountedRef.current) setState(null);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (typeof window !== "undefined") {
      const storedDismissed =
        window.localStorage.getItem("opsmender:setup-checklist-dismissed") === "1";
      queueMicrotask(() => {
        if (mountedRef.current) setDismissed(storedDismissed);
      });
    }
    void Promise.resolve().then(refresh);
    return () => {
      mountedRef.current = false;
    };
  }, [refresh]);

  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [refresh]);

  useLiveEvents(LIVE_REFRESH_CATEGORIES, () => {
    void refresh();
  });

  if (state === null || state.all_complete || dismissed) {
    return null;
  }

  const completedCount = ROWS.filter((row) => state[row.key]).length;
  const progress = Math.round((completedCount / ROWS.length) * 100);

  const handleDismiss = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("opsmender:setup-checklist-dismissed", "1");
    }
    setDismissed(true);
  };

  return (
    <section
      aria-labelledby="setup-checklist-title"
      className="mb-6 rounded-card border border-status-info-border bg-status-info-bg/30 p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2
            id="setup-checklist-title"
            className="text-sm font-semibold text-fg-primary"
          >
            Set up OpsMender
            <span className="ml-2 text-xs font-medium text-fg-muted">
              {completedCount} of {ROWS.length} steps
            </span>
          </h2>
          <p className="mt-0.5 text-xs text-fg-muted">
            Complete the essentials before routing live incidents.
          </p>
          <div
            className="mt-3 h-1.5 overflow-hidden rounded-pill bg-bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={ROWS.length}
            aria-valuenow={completedCount}
            aria-label={`${completedCount} of ${ROWS.length} setup steps complete`}
          >
            <div
              className="h-full rounded-pill bg-status-info transition-[width]"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="mt-2 text-xs font-medium text-fg-muted">
            {completedCount} of {ROWS.length} steps complete
          </p>
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          className="rounded p-1 text-fg-muted hover:text-fg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
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
                href={row.href}
                className={`flex items-center gap-3 rounded px-2 py-1.5 text-sm transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                  done ? "text-fg-muted" : "text-fg-primary"
                }`}
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${
                    done
                      ? "border-status-low-border bg-status-low-bg text-status-low"
                      : "border-border-default text-fg-muted"
                  }`}
                  aria-hidden
                >
                  {done ? (
                    <Check size={12} />
                  ) : (
                    <span className="h-2 w-2 rounded-full bg-current opacity-45" />
                  )}
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
    </section>
  );
}
