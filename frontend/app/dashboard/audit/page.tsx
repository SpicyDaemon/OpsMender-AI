"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, RefreshCw } from "lucide-react";
import { listAudit } from "@/lib/api";
import type { AuditEntryResponse, AuditListResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Label, Select } from "@/components/ui/Input";
import { PageSpinner } from "@/components/ui/Spinner";

const PAGE_SIZE = 25;

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
        className={`h-1.5 w-1.5 rounded-full ${permitted ? "bg-status-low" : "bg-status-critical"}`}
      />
      {permitted ? "Permitted" : "Blocked"}
    </span>
  );
}

export default function AuditPage() {
  const [data, setData] = useState<AuditListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);

  // Filters
  const [sessionId, setSessionId] = useState("");
  const [toolName, setToolName] = useState("");
  const [permitted, setPermitted] = useState<"" | "true" | "false">("");

  const [expanded, setExpanded] = useState<string | null>(null);

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
    } finally {
      setLoading(false);
    }
  }, [sessionId, toolName, permitted, page]);

  useEffect(() => { load(); }, [load]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-fg-primary">Audit Log</h1>
          {data && (
            <p className="text-sm text-fg-secondary mt-0.5">{data.total} entries</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={() => { setPage(0); load(); }} disabled={loading}>
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-3 gap-3 mb-4">
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
          <Label htmlFor="au-perm">Permitted</Label>
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

      {/* Table */}
      {loading && !data ? (
        <PageSpinner />
      ) : data?.items.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={
            sessionId || toolName || permitted
              ? "No audit entries match these filters"
              : "No audit entries yet"
          }
          description={
            sessionId || toolName || permitted
              ? "Try clearing filters above."
              : "Every MCP tool call — permitted or blocked — is recorded here once sessions start running."
          }
        />
      ) : (
        <>
          <div className="overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle bg-bg-elevated text-left text-xs font-medium text-fg-secondary uppercase tracking-wide">
                  <th className="px-4 py-3">Timestamp</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Tool</th>
                  <th className="px-4 py-3">Tier</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Duration</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {data?.items.map((entry) => (
                  <>
                    <tr
                      key={entry.id}
                      className="hover:bg-bg-elevated cursor-pointer transition-colors"
                      onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                    >
                      <td className="px-4 py-3 text-fg-secondary whitespace-nowrap font-mono text-xs">
                        {fmtDate(entry.timestamp)}
                      </td>
                      <td className="px-4 py-3 text-fg-primary">{entry.entry_type}</td>
                      <td className="px-4 py-3 font-mono text-xs text-fg-primary">
                        {entry.tool_name ?? <span className="text-fg-muted">—</span>}
                      </td>
                      <td className="px-4 py-3 text-fg-secondary">{entry.tier}</td>
                      <td className="px-4 py-3">
                        <PermittedDot permitted={entry.permitted} />
                      </td>
                      <td className="px-4 py-3 text-fg-secondary whitespace-nowrap">
                        {entry.duration_ms != null ? `${entry.duration_ms}ms` : "—"}
                      </td>
                    </tr>
                    {/* Expanded detail row */}
                    {expanded === entry.id && (
                      <tr key={`${entry.id}-detail`} className="bg-bg-elevated">
                        <td colSpan={6} className="px-4 py-3">
                          <ExpandedAuditRow entry={entry} />
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm text-fg-secondary">
              <span>
                Page {page + 1} of {totalPages}
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

function ExpandedAuditRow({ entry }: { entry: AuditEntryResponse }) {
  return (
    <div className="grid grid-cols-2 gap-4 text-xs">
      <div>
        <p className="font-medium text-fg-secondary mb-1">Session ID</p>
        <p className="font-mono text-fg-primary">{entry.session_id}</p>
      </div>
      {entry.block_reason && (
        <div>
          <p className="font-medium text-status-critical mb-1">Block Reason</p>
          <p className="text-status-critical">{entry.block_reason}</p>
        </div>
      )}
      {entry.tool_parameters && (
        <div>
          <p className="font-medium text-fg-secondary mb-1">Parameters</p>
          <pre className="bg-bg-elevated rounded p-2 overflow-x-auto">
            {JSON.stringify(entry.tool_parameters, null, 2)}
          </pre>
        </div>
      )}
      {entry.result && (
        <div>
          <p className="font-medium text-fg-secondary mb-1">Result</p>
          <pre className="bg-bg-elevated rounded p-2 overflow-x-auto">
            {JSON.stringify(entry.result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
