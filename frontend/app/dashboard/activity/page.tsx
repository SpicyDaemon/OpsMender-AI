"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  Calendar,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Columns3,
  Download,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { downloadAuditCsv, listAudit } from "@/lib/api";
import type { AuditEntryResponse, AuditListResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { FilterDropdown } from "@/components/ui/FilterDropdown";
import { Input } from "@/components/ui/Input";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { Toggle } from "@/components/ui/Toggle";
import { useToast } from "@/components/ui/Toast";
import { auditEntryTypeLabel } from "@/lib/displayNames";
import { formatDateTime, formatTime } from "@/lib/formatDate";

const FETCH_LIMIT = 500;

const TYPE_FILTER_OPTIONS = [
  { value: "session_start", label: "Session started" },
  { value: "session_end", label: "Session ended" },
  { value: "pre", label: "Tool call" },
  { value: "post", label: "Tool result" },
];

const TIER_FILTER_OPTIONS = [
  { value: "0", label: "Tier 0" },
  { value: "1", label: "Tier 1" },
  { value: "2", label: "Tier 2" },
];

const STATUS_FILTER_OPTIONS = [
  { value: "permitted", label: "Permitted" },
  { value: "blocked", label: "Blocked" },
];

function fmtDate(iso: string) {
  return formatDateTime(iso);
}

function fmtDuration(ms: number | null) {
  return ms != null ? `${ms}ms` : "—";
}

function entryTimeMs(entry: AuditEntryResponse) {
  const parsed = new Date(entry.timestamp).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function shortSessionId(sessionId: string | null) {
  return sessionId ? `${sessionId.slice(0, 8)}…` : "System";
}

function countLabel(count: number) {
  return `${count} ${count === 1 ? "entry" : "entries"}`;
}

function toggleValue(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

export interface ActivitySessionGroup {
  key: string;
  sessionId: string | null;
  entries: AuditEntryResponse[];
  blockedCount: number;
  startedAt: string;
  endedAt: string;
  latestMs: number;
}

export function groupAuditEntriesBySession(
  entries: AuditEntryResponse[],
): ActivitySessionGroup[] {
  const groups = new Map<string, ActivitySessionGroup & { earliestMs: number }>();

  for (const entry of entries) {
    const sessionId = entry.session_id || null;
    const key = sessionId ? `session:${sessionId}` : "system";
    const ms = entryTimeMs(entry);
    const existing = groups.get(key);
    if (existing) {
      existing.entries.push(entry);
      existing.blockedCount += entry.permitted ? 0 : 1;
      if (ms < existing.earliestMs) {
        existing.earliestMs = ms;
        existing.startedAt = entry.timestamp;
      }
      if (ms > existing.latestMs) {
        existing.latestMs = ms;
        existing.endedAt = entry.timestamp;
      }
    } else {
      groups.set(key, {
        key,
        sessionId,
        entries: [entry],
        blockedCount: entry.permitted ? 0 : 1,
        startedAt: entry.timestamp,
        endedAt: entry.timestamp,
        earliestMs: ms,
        latestMs: ms,
      });
    }
  }

  return Array.from(groups.values())
    .map(({ earliestMs: _earliestMs, ...group }) => ({
      ...group,
      entries: [...group.entries].sort((a, b) => entryTimeMs(b) - entryTimeMs(a)),
    }))
    .sort((a, b) => b.latestMs - a.latestMs);
}

function entryMatchesGroupedFilters(
  entry: AuditEntryResponse,
  {
    search,
    typeFilters,
    tierFilters,
    statusFilters,
  }: {
    search: string;
    typeFilters: string[];
    tierFilters: string[];
    statusFilters: string[];
  },
) {
  if (typeFilters.length > 0 && !typeFilters.includes(entry.entry_type)) {
    return false;
  }
  if (tierFilters.length > 0 && !tierFilters.includes(String(entry.tier))) {
    return false;
  }
  const status = entry.permitted ? "permitted" : "blocked";
  if (statusFilters.length > 0 && !statusFilters.includes(status)) {
    return false;
  }

  const q = search.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    entry.timestamp,
    fmtDate(entry.timestamp),
    auditEntryTypeLabel(entry.entry_type),
    entry.tool_name,
    entry.session_id,
    String(entry.tier),
    status,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
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
          cls = "text-accent-text"; // key
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
  const [groupBySession, setGroupBySession] = useState(true);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [groupSearch, setGroupSearch] = useState("");
  const [groupTypeFilters, setGroupTypeFilters] = useState<string[]>([]);
  const [groupTierFilters, setGroupTierFilters] = useState<string[]>([]);
  const [groupStatusFilters, setGroupStatusFilters] = useState<string[]>([]);
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
  const groupedFilteredEntries = useMemo(
    () =>
      entries.filter((entry) =>
        entryMatchesGroupedFilters(entry, {
          search: groupSearch,
          typeFilters: groupTypeFilters,
          tierFilters: groupTierFilters,
          statusFilters: groupStatusFilters,
        }),
      ),
    [entries, groupSearch, groupTypeFilters, groupTierFilters, groupStatusFilters],
  );
  const activityGroups = useMemo(
    () => groupAuditEntriesBySession(groupedFilteredEntries),
    [groupedFilteredEntries],
  );

  useEffect(() => {
    if (!groupBySession || activityGroups.length === 0) return;
    setExpandedGroups((current) => {
      const visibleKeys = new Set(activityGroups.map((group) => group.key));
      const stillVisible = Array.from(current).filter((key) => visibleKeys.has(key));
      if (stillVisible.length > 0) return new Set(stillVisible);
      return new Set([activityGroups[0].key]);
    });
  }, [activityGroups, groupBySession]);

  function toggleGroup(key: string) {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

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
        accessor: (entry) => auditEntryTypeLabel(entry.entry_type),
        cell: (entry) => (
          <span className="inline-flex items-center whitespace-nowrap rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-secondary">
            {auditEntryTypeLabel(entry.entry_type)}
          </span>
        ),
        sortable: true,
        searchable: true,
        filterChips: {
          options: [
            { value: "session_start", label: "Session started" },
            { value: "session_end", label: "Session ended" },
            { value: "pre", label: "Tool call" },
            { value: "post", label: "Tool result" },
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
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex h-9 items-center gap-2 rounded-md border border-border-subtle bg-bg-panel px-2.5">
                <Toggle
                  checked={groupBySession}
                  onChange={setGroupBySession}
                  aria-label="Group by session"
                />
                <span className="text-xs font-medium text-fg-secondary">
                  Group by session
                </span>
              </div>
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
        groupBySession ? (
          <div className="space-y-3">
            <GroupedActivityToolbar
              search={groupSearch}
              onSearchChange={setGroupSearch}
              typeFilters={groupTypeFilters}
              onToggleType={(value) =>
                setGroupTypeFilters((current) => toggleValue(current, value))
              }
              tierFilters={groupTierFilters}
              onToggleTier={(value) =>
                setGroupTierFilters((current) => toggleValue(current, value))
              }
              statusFilters={groupStatusFilters}
              onToggleStatus={(value) =>
                setGroupStatusFilters((current) => toggleValue(current, value))
              }
              onClear={() => {
                setGroupSearch("");
                setGroupTypeFilters([]);
                setGroupTierFilters([]);
                setGroupStatusFilters([]);
              }}
            />
            {activityGroups.length === 0 ? (
              <EmptyState
                icon={BookOpen}
                title="No activity matches"
                description="Clear the grouped filters or switch to the flat table to use date range and column controls."
              />
            ) : (
              <GroupedActivityList
                groups={activityGroups}
                expandedGroups={expandedGroups}
                onToggleGroup={toggleGroup}
              />
            )}
          </div>
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
        )
      )}
    </div>
  );
}

function GroupedActivityToolbar({
  search,
  onSearchChange,
  typeFilters,
  onToggleType,
  tierFilters,
  onToggleTier,
  statusFilters,
  onToggleStatus,
  onClear,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  typeFilters: string[];
  onToggleType: (value: string) => void;
  tierFilters: string[];
  onToggleTier: (value: string) => void;
  statusFilters: string[];
  onToggleStatus: (value: string) => void;
  onClear: () => void;
}) {
  const hasActiveFilters =
    Boolean(search) ||
    typeFilters.length > 0 ||
    tierFilters.length > 0 ||
    statusFilters.length > 0;

  return (
    <div className="rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
            aria-hidden
          />
          <Input
            aria-label="Search grouped activity"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search timestamp, type, tool, or session…"
            className="h-11 pl-9"
          />
        </div>
        <FilterDropdown
          label="Type"
          options={TYPE_FILTER_OPTIONS}
          selected={typeFilters}
          onToggle={onToggleType}
        />
        <FilterDropdown
          label="Tier"
          options={TIER_FILTER_OPTIONS}
          selected={tierFilters}
          onToggle={onToggleTier}
        />
        <FilterDropdown
          label="Status"
          options={STATUS_FILTER_OPTIONS}
          selected={statusFilters}
          onToggle={onToggleStatus}
        />
        <Button
          variant="secondary"
          className="h-11"
          disabled
          title="Date range is available in flat table mode."
        >
          <Calendar size={14} />
          Date range
        </Button>
        <Button
          variant="secondary"
          className="h-11"
          disabled
          title="Column picker is available in flat table mode."
        >
          <Columns3 size={14} />
          Columns
        </Button>
        <div className="ml-auto">
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClear}
              className="h-11"
              title="Clear grouped search and filters"
            >
              <X size={14} />
              Clear
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function GroupedActivityList({
  groups,
  expandedGroups,
  onToggleGroup,
}: {
  groups: ActivitySessionGroup[];
  expandedGroups: Set<string>;
  onToggleGroup: (key: string) => void;
}) {
  return (
    <div className="space-y-3">
      {groups.map((group) => {
        const expanded = expandedGroups.has(group.key);
        const panelId = `activity-group-${group.key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
        return (
          <section
            key={group.key}
            className="overflow-hidden rounded-lg border border-border-subtle bg-bg-panel shadow-sm"
          >
            <div className="flex flex-col gap-3 border-b border-border-subtle bg-bg-elevated/60 p-4 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="button"
                className="flex min-w-0 flex-1 items-start gap-3 text-left"
                aria-expanded={expanded}
                aria-controls={panelId}
                onClick={() => onToggleGroup(group.key)}
              >
                <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-bg-panel text-fg-muted">
                  {expanded ? (
                    <ChevronDown size={15} aria-hidden />
                  ) : (
                    <ChevronRight size={15} aria-hidden />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-fg-primary">
                      {shortSessionId(group.sessionId)}
                    </span>
                    <span className="rounded-pill border border-border-subtle bg-bg-panel px-2 py-0.5 text-[11px] font-medium text-fg-secondary">
                      {countLabel(group.entries.length)}
                    </span>
                    {group.blockedCount > 0 && (
                      <span className="rounded-pill bg-status-critical-bg px-2 py-0.5 text-[11px] font-medium text-status-critical">
                        {group.blockedCount} blocked
                      </span>
                    )}
                  </span>
                  <span className="mt-1 block text-xs text-fg-muted">
                    {formatDateTime(group.startedAt)} → {formatTime(group.endedAt)}
                  </span>
                </span>
              </button>
              {group.sessionId && (
                <Link
                  href={`/dashboard/sessions/detail?id=${group.sessionId}`}
                  className="inline-flex items-center justify-center rounded-md border border-border-subtle bg-bg-panel px-3 py-2 text-xs font-medium text-fg-primary hover:bg-bg-hover"
                >
                  View session
                </Link>
              )}
            </div>
            {expanded && (
              <div id={panelId} className="divide-y divide-border-subtle">
                {group.entries.map((entry) => (
                  <GroupedActivityRow key={entry.id} entry={entry} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function GroupedActivityRow({ entry }: { entry: AuditEntryResponse }) {
  return (
    <div className="grid gap-3 px-4 py-3 text-sm md:grid-cols-[minmax(11rem,auto)_minmax(9rem,auto)_minmax(8rem,1fr)_4rem_minmax(7rem,auto)_5rem] md:items-center">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Timestamp
        </p>
        <span className="whitespace-nowrap font-mono text-xs tabular-nums text-fg-secondary">
          {fmtDate(entry.timestamp)}
        </span>
      </div>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Type
        </p>
        <span className="inline-flex items-center whitespace-nowrap rounded-pill border border-border-subtle bg-bg-elevated px-2 py-0.5 text-[11px] font-medium text-fg-secondary">
          {auditEntryTypeLabel(entry.entry_type)}
        </span>
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Tool
        </p>
        <span className="break-all font-mono text-xs text-fg-primary">
          {entry.tool_name ?? <span className="text-fg-muted">—</span>}
        </span>
      </div>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Tier
        </p>
        <span className="text-xs tabular-nums text-fg-secondary">{entry.tier}</span>
      </div>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Status
        </p>
        <PermittedDot permitted={entry.permitted} />
      </div>
      <div className="md:text-right">
        <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted md:hidden">
          Duration
        </p>
        <span className="whitespace-nowrap font-mono text-xs tabular-nums text-fg-secondary">
          {fmtDuration(entry.duration_ms)}
        </span>
      </div>
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
