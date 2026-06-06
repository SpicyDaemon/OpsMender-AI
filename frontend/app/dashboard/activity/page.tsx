"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  ClipboardCopy,
  Download,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { downloadAuditCsv, listAudit } from "@/lib/api";
import type { AuditEntryResponse, AuditListResponse } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

const FETCH_LIMIT = 500;

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
// Page
// ---------------------------------------------------------------------------

export default function ActivityPage() {
  const [data, setData] = useState<AuditListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const toast = useToast();

  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      await downloadAuditCsv();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to export CSV");
    } finally {
      setExporting(false);
    }
  }, [toast]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAudit({ limit: FETCH_LIMIT });
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const entries = data?.items ?? [];
  const expandedKeys = useMemo(
    () => (expanded ? new Set([expanded]) : new Set<string>()),
    [expanded],
  );

  const columns = useMemo<DataTableColumn<AuditEntryResponse>[]>(
    () => [
      {
        id: "timestamp",
        label: "Timestamp",
        accessor: (entry) => entry.timestamp,
        cell: (entry) => (
          <span className="whitespace-nowrap font-mono text-xs tabular-nums text-fg-secondary">
            {fmtDate(entry.timestamp)}
          </span>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "entry_type",
        label: "Type",
        accessor: (entry) => entry.entry_type,
        cell: (entry) => (
          <Badge variant={entry.entry_type === "pre" ? "info" : "default"}>
            {entry.entry_type}
          </Badge>
        ),
        sortable: true,
        searchable: true,
        filterChips: {
          options: [
            { value: "pre", label: "Pre" },
            { value: "post", label: "Post" },
          ],
          valueOf: (entry) => entry.entry_type,
        },
      },
      {
        id: "tool_name",
        label: "Tool",
        accessor: (entry) => entry.tool_name ?? "",
        cell: (entry) => (
          <span className="font-mono text-xs text-fg-primary">
            {entry.tool_name ?? <span className="text-fg-muted">—</span>}
          </span>
        ),
        sortable: true,
        searchable: true,
      },
      {
        id: "tier",
        label: "Tier",
        accessor: (entry) => entry.tier,
        cell: (entry) => (
          <span className="text-xs tabular-nums text-fg-secondary">
            {entry.tier}
          </span>
        ),
        sortable: true,
        filterChips: {
          options: [
            { value: "0", label: "Tier 0" },
            { value: "1", label: "Tier 1" },
            { value: "2", label: "Tier 2" },
          ],
          valueOf: (entry) => String(entry.tier),
        },
      },
      {
        id: "status",
        label: "Status",
        accessor: (entry) => (entry.permitted ? "permitted" : "blocked"),
        cell: (entry) => <PermittedDot permitted={entry.permitted} />,
        sortable: true,
        filterChips: {
          options: [
            { value: "permitted", label: "Permitted" },
            { value: "blocked", label: "Blocked" },
          ],
          valueOf: (entry) => (entry.permitted ? "permitted" : "blocked"),
        },
      },
      {
        id: "duration",
        label: "Duration",
        accessor: (entry) => entry.duration_ms ?? null,
        cell: (entry) => (
          <span className="whitespace-nowrap font-mono text-xs tabular-nums text-fg-secondary">
            {entry.duration_ms != null ? `${entry.duration_ms}ms` : "—"}
          </span>
        ),
        sortable: true,
        align: "right",
      },
      {
        id: "session_id",
        label: "Session",
        accessor: (entry) => entry.session_id,
        cell: (entry) => (
          <span className="font-mono text-xs text-fg-muted">
            {entry.session_id?.slice(0, 8) ?? "—"}
          </span>
        ),
        searchable: true,
        hiddenByDefault: true,
      },
    ],
    [],
  );

  return (
    <div>
      <div className="mb-6">
        <PageHeader
          title="Activity"
          subtitle={
            data
              ? data.total > entries.length
                ? `${entries.length} latest of ${data.total} entries`
                : `${data.total} entries`
              : undefined
          }
          icon={<BookOpen size={18} />}
          actions={
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleExport}
                disabled={exporting || entries.length === 0}
              >
                <Download size={14} className={exporting ? "animate-pulse" : ""} />
                {exporting ? "Exporting…" : "Download CSV"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={load}
                disabled={loading}
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                Refresh
              </Button>
            </div>
          }
        />
      </div>

      {loading && !data ? (
        <TableSkeleton rows={8} columns={6} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title="No audit entries yet"
          description="Every MCP tool call — permitted or blocked — is recorded here once sessions start running. Fire a test incident to generate a first session and watch tool calls land here."
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            <Link
              href="/dashboard/incidents?test=1"
              className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-bg-panel px-2.5 py-1 text-xs font-medium text-fg-primary transition-colors hover:bg-bg-hover"
            >
              <Sparkles size={14} />
              Fire test incident
            </Link>
          }
        />
      ) : (
        <DataTable
          rows={entries}
          columns={columns}
          rowKey={(entry) => entry.id}
          storageKey="opsmender:activity-table"
          filterBar
          searchPlaceholder="Search timestamp, type, tool, or session…"
          dateRangeColumn={{
            id: "timestamp",
            label: "Timestamp",
            valueOf: (entry) => entry.timestamp,
          }}
          expandedRow={{
            expandedKeys,
            onToggle: (key) => setExpanded((cur) => (cur === key ? null : key)),
            render: (entry) => <ExpandedAuditRow entry={entry} />,
            label: "Activity details",
          }}
        />
      )}
    </div>
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
