"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { listAudit } from "@/lib/api";
import type { AuditEntryResponse, AuditListResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
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
        permitted ? "text-green-700" : "text-red-700"
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${permitted ? "bg-green-500" : "bg-red-500"}`}
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
          <h1 className="text-2xl font-bold text-gray-900">Audit Log</h1>
          {data && (
            <p className="text-sm text-gray-500 mt-0.5">{data.total} entries</p>
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
      ) : (
        <>
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">Timestamp</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Tool</th>
                  <th className="px-4 py-3">Tier</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Duration</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data?.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                      No audit entries found.
                    </td>
                  </tr>
                )}
                {data?.items.map((entry) => (
                  <>
                    <tr
                      key={entry.id}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                    >
                      <td className="px-4 py-3 text-gray-500 whitespace-nowrap font-mono text-xs">
                        {fmtDate(entry.timestamp)}
                      </td>
                      <td className="px-4 py-3 text-gray-700">{entry.entry_type}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-700">
                        {entry.tool_name ?? <span className="text-gray-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-gray-500">{entry.tier}</td>
                      <td className="px-4 py-3">
                        <PermittedDot permitted={entry.permitted} />
                      </td>
                      <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                        {entry.duration_ms != null ? `${entry.duration_ms}ms` : "—"}
                      </td>
                    </tr>
                    {/* Expanded detail row */}
                    {expanded === entry.id && (
                      <tr key={`${entry.id}-detail`} className="bg-gray-50">
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
            <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
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
        <p className="font-medium text-gray-500 mb-1">Session ID</p>
        <p className="font-mono text-gray-700">{entry.session_id}</p>
      </div>
      {entry.block_reason && (
        <div>
          <p className="font-medium text-red-500 mb-1">Block Reason</p>
          <p className="text-red-700">{entry.block_reason}</p>
        </div>
      )}
      {entry.tool_parameters && (
        <div>
          <p className="font-medium text-gray-500 mb-1">Parameters</p>
          <pre className="bg-gray-100 rounded p-2 overflow-x-auto">
            {JSON.stringify(entry.tool_parameters, null, 2)}
          </pre>
        </div>
      )}
      {entry.result && (
        <div>
          <p className="font-medium text-gray-500 mb-1">Result</p>
          <pre className="bg-gray-100 rounded p-2 overflow-x-auto">
            {JSON.stringify(entry.result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
