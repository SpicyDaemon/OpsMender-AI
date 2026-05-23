"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Columns3,
  RefreshCw,
  X,
} from "lucide-react";
import { listAudit } from "@/lib/api";
import type { AuditEntryResponse, AuditListResponse } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const PAGE_SIZE = 25;

const ALL_COLUMNS = [
  { key: "timestamp", label: "Timestamp", default: true },
  { key: "entry_type", label: "Type", default: true },
  { key: "tool_name", label: "Tool", default: true },
  { key: "tier", label: "Tier", default: true },
  { key: "status", label: "Status", default: true },
  { key: "duration", label: "Duration", default: true },
  { key: "session_id", label: "Session", default: false },
] as const;

type ColumnKey = (typeof ALL_COLUMNS)[number]["key"];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function PermittedDot({ permitted }: { permitted: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium ${
        permitted ? "text-status-low" : "text-status-critical"
      }`}
    >
      <span
        className={`h-2 w-2 rounded-full ${permitted ? "bg-status-low" : "bg-status-critical"}`}
      />
      {permitted ? "Permitted" : "Blocked"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Syntax-highlighted JSON viewer
// ---------------------------------------------------------------------------

function JsonHighlight({ data }: { data: unknown }) {
  const json = JSON.stringify(data, null, 2);
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  // Simple token-based highlighting
  const highlighted = json.replace(
    /("(\\u[\da-fA-F]{4}|\\[^u]|[^"\\])*")\s*:?|(\b(true|false|null)\b)|(-?\d+(\.\d+)?([eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "text-status-medium"; // number
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = "text-accent"; // key
          return `<span class="${cls}">${escapeHtml(match.slice(0, -1))}</span>:`;
        }
        cls = "text-status-low"; // string value
      } else if (/true|false/.test(match)) {
        cls = "text-status-high"; // boolean
      } else if (/null/.test(match)) {
        cls = "text-fg-muted"; // null
      }
      return `<span class="${cls}">${escapeHtml(match)}</span>`;
    },
  );

  return (
    <div className="relative group rounded-lg border border-border-subtle bg-bg-base overflow-hidden">
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1 text-[10px] text-fg-muted hover:text-fg-primary bg-bg-elevated border border-border-subtle rounded px-2 py-1"
      >
        <ClipboardCopy size={10} />
        {copied ? "Copied!" : "Copy"}
      </button>
      <pre
        className="px-4 py-3 overflow-x-auto text-xs font-mono leading-relaxed text-fg-primary"
        dangerouslySetInnerHTML={{ __html: highlighted }}
      />
    </div>
  );
}

function escapeHtml(str: string): string {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Column visibility popover
// ---------------------------------------------------------------------------

function ColumnToggle({
  visible,
  onChange,
}: {
  visible: Set<ColumnKey>;
  onChange: (next: Set<ColumnKey>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  function toggle(key: ColumnKey) {
    const next = new Set(visible);
    if (next.has(key)) {
      if (next.size > 2) next.delete(key); // keep at least 2 columns
    } else {
      next.add(key);
    }
    onChange(next);
  }

  return (
    <div className="relative" ref={ref}>
      <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
        <Columns3 size={14} />
        Columns
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 w-48 rounded-lg border border-border-subtle bg-bg-panel shadow-lg p-2 space-y-0.5">
          {ALL_COLUMNS.map((col) => (
            <label
              key={col.key}
              className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-bg-hover cursor-pointer text-sm text-fg-primary"
            >
              <input
                type="checkbox"
                checked={visible.has(col.key)}
                onChange={() => toggle(col.key)}
                className="rounded border-border-strong accent-accent"
              />
              {col.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ActivityPage() {
  const [data, setData] = useState<AuditListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);

  // Filters
  const [sessionId, setSessionId] = useState("");
  const [toolName, setToolName] = useState("");
  const [permitted, setPermitted] = useState<"" | "true" | "false">("");

  const [expanded, setExpanded] = useState<string | null>(null);
  const [visibleCols, setVisibleCols] = useState<Set<ColumnKey>>(
    () => new Set(ALL_COLUMNS.filter((c) => c.default).map((c) => c.key)),
  );
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAudit({
        session_id: sessionId.trim() || undefined,
        tool_name: toolName.trim() || undefined,
        permitted: permitted === "" ? undefined : permitted === "true",
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }, [sessionId, toolName, permitted, page, toast]);

  useEffect(() => { load(); }, [load]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const hasFilters = sessionId || toolName || permitted;

  function clearFilters() {
    setSessionId("");
    setToolName("");
    setPermitted("");
    setPage(0);
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6">
        <PageHeader
          title="Activity"
          subtitle={data ? `${data.total} entries` : undefined}
          icon={<BookOpen size={18} />}
          actions={
            <>
              <ColumnToggle visible={visibleCols} onChange={setVisibleCols} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPage(0);
                  load();
                }}
                disabled={loading}
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                Refresh
              </Button>
            </>
          }
        />
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-border-subtle bg-bg-panel p-4 mb-5">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <Label htmlFor="au-session">Session ID</Label>
            <Input
              id="au-session"
              placeholder="uuid…"
              value={sessionId}
              onChange={(e) => { setSessionId(e.target.value); setPage(0); }}
            />
          </div>
          <div>
            <Label htmlFor="au-tool">Tool Name</Label>
            <Input
              id="au-tool"
              placeholder="kubectl_get"
              value={toolName}
              onChange={(e) => { setToolName(e.target.value); setPage(0); }}
            />
          </div>
          <div>
            <Label htmlFor="au-perm">Permission</Label>
            <Select
              id="au-perm"
              value={permitted}
              onChange={(e) => { setPermitted(e.target.value as "" | "true" | "false"); setPage(0); }}
            >
              <option value="">All</option>
              <option value="true">Permitted</option>
              <option value="false">Blocked</option>
            </Select>
          </div>
        </div>
        {hasFilters && (
          <button
            onClick={clearFilters}
            className="mt-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-primary transition-colors"
          >
            <X size={12} />
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      {loading && !data ? (
        <TableSkeleton rows={8} columns={6} />
      ) : data?.items.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={
            hasFilters
              ? "No audit entries match these filters"
              : "No audit entries yet"
          }
          description={
            hasFilters
              ? "Try clearing filters above."
              : "Every MCP tool call — permitted or blocked — is recorded here once sessions start running."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
        />
      ) : (
        <>
          <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
                  <th className="px-3 py-3 w-8"></th>
                  {visibleCols.has("timestamp") && <th className="px-4 py-3">Timestamp</th>}
                  {visibleCols.has("entry_type") && <th className="px-4 py-3">Type</th>}
                  {visibleCols.has("tool_name") && <th className="px-4 py-3">Tool</th>}
                  {visibleCols.has("tier") && <th className="px-4 py-3">Tier</th>}
                  {visibleCols.has("status") && <th className="px-4 py-3">Status</th>}
                  {visibleCols.has("duration") && <th className="px-4 py-3">Duration</th>}
                  {visibleCols.has("session_id") && <th className="px-4 py-3">Session</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {data?.items.map((entry) => {
                  const isExpanded = expanded === entry.id;
                  return (
                    <AuditRow
                      key={entry.id}
                      entry={entry}
                      isExpanded={isExpanded}
                      visibleCols={visibleCols}
                      onToggle={() => setExpanded(isExpanded ? null : entry.id)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm text-fg-secondary">
              <span className="tabular-nums font-mono text-xs">
                Page {page + 1} of {totalPages} · {data?.total} total
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table row with expandable detail
// ---------------------------------------------------------------------------

function AuditRow({
  entry,
  isExpanded,
  visibleCols,
  onToggle,
}: {
  entry: AuditEntryResponse;
  isExpanded: boolean;
  visibleCols: Set<ColumnKey>;
  onToggle: () => void;
}) {
  const colCount = 1 + visibleCols.size; // +1 for expand column

  return (
    <>
      <tr
        className={`cursor-pointer transition-colors ${
          isExpanded ? "bg-bg-elevated" : "hover:bg-bg-elevated"
        } ${!entry.permitted ? "border-l-2 border-l-status-critical/40" : ""}`}
        onClick={onToggle}
      >
        <td className="px-3 py-3 text-fg-muted">
          {isExpanded ? (
            <ChevronDown size={14} className="text-fg-secondary" />
          ) : (
            <ChevronRight size={14} />
          )}
        </td>
        {visibleCols.has("timestamp") && (
          <td className="px-4 py-3 text-fg-secondary whitespace-nowrap font-mono text-xs tabular-nums">
            {fmtDate(entry.timestamp)}
          </td>
        )}
        {visibleCols.has("entry_type") && (
          <td className="px-4 py-3">
            <Badge variant={entry.entry_type === "pre" ? "info" : "default"}>
              {entry.entry_type}
            </Badge>
          </td>
        )}
        {visibleCols.has("tool_name") && (
          <td className="px-4 py-3 font-mono text-xs text-fg-primary">
            {entry.tool_name ?? <span className="text-fg-muted">—</span>}
          </td>
        )}
        {visibleCols.has("tier") && (
          <td className="px-4 py-3 text-fg-secondary text-xs tabular-nums">{entry.tier}</td>
        )}
        {visibleCols.has("status") && (
          <td className="px-4 py-3">
            <PermittedDot permitted={entry.permitted} />
          </td>
        )}
        {visibleCols.has("duration") && (
          <td className="px-4 py-3 text-fg-secondary whitespace-nowrap text-xs tabular-nums font-mono">
            {entry.duration_ms != null ? `${entry.duration_ms}ms` : "—"}
          </td>
        )}
        {visibleCols.has("session_id") && (
          <td className="px-4 py-3 font-mono text-xs text-fg-muted">
            {entry.session_id?.slice(0, 8) ?? "—"}
          </td>
        )}
      </tr>
      {/* Expanded detail row */}
      {isExpanded && (
        <tr className="bg-bg-elevated/60">
          <td colSpan={colCount} className="px-4 py-4">
            <ExpandedAuditRow entry={entry} />
          </td>
        </tr>
      )}
    </>
  );
}

function ExpandedAuditRow({ entry }: { entry: AuditEntryResponse }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
      <div className="space-y-3">
        <div>
          <p className="font-medium text-fg-secondary mb-1.5">Session ID</p>
          <p className="font-mono text-fg-primary select-all">{entry.session_id}</p>
        </div>
        {entry.block_reason && (
          <div className="rounded-lg border border-status-critical-border bg-status-critical-bg/30 p-3">
            <p className="font-medium text-status-critical mb-1">Block Reason</p>
            <p className="text-status-critical">{entry.block_reason}</p>
          </div>
        )}
      </div>
      <div className="space-y-3">
        {entry.tool_parameters && (
          <div>
            <p className="font-medium text-fg-secondary mb-1.5">Parameters</p>
            <JsonHighlight data={entry.tool_parameters} />
          </div>
        )}
        {entry.result && (
          <div>
            <p className="font-medium text-fg-secondary mb-1.5">Result</p>
            <JsonHighlight data={entry.result} />
          </div>
        )}
      </div>
    </div>
  );
}
