"use client";

import { type ReactNode, useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import {
  bulkIncidentAction,
  combineIncidents,
  createIncident,
  deleteIncident,
  fireTestIncident,
  listIncidents,
  listServices,
  listTeams,
  updateIncident,
} from "@/lib/api";
import { SetupChecklist } from "@/components/SetupChecklist";
import { useAuth } from "@/context/auth";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";
import { responderDisplay } from "@/lib/responder";
import type {
  FireTestIncidentResponse,
  IncidentCreate,
  IncidentCreateResponse,
  IncidentListResponse,
  IncidentResponse,
  IncidentStatus,
  ServiceResponse,
  Severity,
  TeamResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterDropdown } from "@/components/ui/FilterDropdown";
import { Input, Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { formatDateTime, formatRelative } from "@/lib/formatDate";

function fmtDate(iso: string) {
  return formatDateTime(iso);
}

const fmtRelative = formatRelative;

function isSyntheticTestIncident(incident: IncidentResponse) {
  return incident.external_source === "opsmender-test";
}

function displayValue(value: string | null | undefined) {
  if (!value) return "Unknown";
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function sourceMeta(incident: IncidentResponse) {
  if (!incident.external_source) {
    return {
      key: "manual",
      label: "Manual",
      icon: "M",
      className: "bg-status-info-bg text-status-info border-status-info-border",
    };
  }
  if (incident.external_source === "opsmender-test") {
    return {
      key: "ingested",
      label: "Test",
      icon: "T",
      className: "bg-status-high-bg text-status-high border-status-high-border",
    };
  }
  const raw = incident.external_source.replace(/^auto:/, "").replace(/_/g, " ");
  const label = raw
    .split(":")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" / ");
  return {
    key: "ingested",
    label: label || "Ingested",
    icon: (label || "I").slice(0, 2).toUpperCase(),
    className: "bg-status-medium-bg text-status-medium border-status-medium-border",
  };
}

const STATUS_OPTIONS: { value: IncidentStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "resolved", label: "Resolved" },
];

const SEVERITY_OPTIONS: { value: Severity | ""; label: string }[] = [
  { value: "", label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const SOURCE_OPTIONS = [
  { value: "", label: "All sources" },
  { value: "manual", label: "Manual" },
  { value: "ingested", label: "Ingested" },
];

type TimePreset = "all" | "15m" | "60m" | "3h" | "6h" | "24h" | "today" | "yesterday" | "7d" | "30d" | "custom";

const TIME_PRESETS: { value: TimePreset; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "15m", label: "Last 15 minutes" },
  { value: "60m", label: "Last 60 minutes" },
  { value: "3h", label: "Last 3 hours" },
  { value: "6h", label: "Last 6 hours" },
  { value: "24h", label: "Last 24 hours" },
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

function timeRangeForPreset(preset: TimePreset) {
  const now = new Date();
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  if (preset === "all" || preset === "custom") return { from: "", to: "" };
  if (preset === "today") return { from: startOfToday.toISOString(), to: now.toISOString() };
  if (preset === "yesterday") {
    const from = new Date(startOfToday);
    from.setDate(from.getDate() - 1);
    const to = new Date(startOfToday);
    return { from: from.toISOString(), to: to.toISOString() };
  }
  const minutesByPreset: Record<Exclude<TimePreset, "all" | "today" | "yesterday" | "custom">, number> = {
    "15m": 15,
    "60m": 60,
    "3h": 180,
    "6h": 360,
    "24h": 1440,
    "7d": 10080,
    "30d": 43200,
  };
  const from = new Date(now.getTime() - minutesByPreset[preset] * 60_000);
  return { from: from.toISOString(), to: now.toISOString() };
}

/** In-progress AI-session pill for the incidents list. Renders only while a
 * session is queued, active, or awaiting approval; terminal/absent sessions show
 * nothing so the list stays uncluttered. */
function AiSessionBadge({ incident }: { incident: IncidentResponse }) {
  if (!incident.ai_session_active || !incident.ai_session_status) return null;
  const awaiting = incident.ai_session_status === "awaiting_approval";
  const queued = incident.ai_session_status === "queued";
  return (
    <Badge
      variant={queued ? "queued" : awaiting ? "awaiting_approval" : "active"}
      className="gap-1"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      {queued ? "AI · waiting" : awaiting ? "AI · approval" : "AI · running"}
    </Badge>
  );
}

function buildIncidentColumns(): DataTableColumn<IncidentResponse>[] {
  return [
    {
      id: "title",
      label: "Incident",
      accessor: (inc) => inc.title,
      cell: (inc) => (
        <div>
          <div className="flex items-center gap-2">
            <Link
              href={`/dashboard/incidents/detail?id=${inc.id}`}
              className="font-medium text-fg-primary hover:text-accent-text"
            >
              {inc.title}
            </Link>
            <AiSessionBadge incident={inc} />
          </div>
          <p className="mt-0.5 max-w-md truncate text-xs text-fg-muted">
            {inc.description}
          </p>
          <p className="mt-1 text-[11px] text-fg-muted">
            Opened {fmtDate(inc.created_at)}
          </p>
        </div>
      ),
      sortable: true,
    },
    {
      id: "team",
      label: "Team",
      accessor: (inc) => inc.team_name ?? "",
      cell: (inc) => {
        return inc.team_name ? (
          <div>
            <p className="text-sm text-fg-primary">{inc.team_name}</p>
            {inc.service_name ? (
              <p className="mt-0.5 text-[11px] text-fg-muted">{inc.service_name}</p>
            ) : null}
          </div>
        ) : (
          <span className="text-fg-muted">—</span>
        );
      },
      sortable: true,
    },
    {
      id: "responder",
      label: "Responder",
      accessor: (inc) => responderDisplay(inc).text,
      cell: (inc) => {
        const r = responderDisplay(inc);
        const cls =
          r.tone === "ok"
            ? "text-status-low"
            : r.tone === "warn"
              ? "text-status-medium"
              : "text-fg-muted";
        return <span className={`text-sm ${cls}`}>{r.text}</span>;
      },
      sortable: true,
    },
    {
      id: "source",
      label: "Source",
      accessor: (inc) => sourceMeta(inc).key,
      cell: (inc) => {
        const source = sourceMeta(inc);
        return (
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex h-7 min-w-7 items-center justify-center rounded-md border px-1 text-[10px] font-semibold uppercase tracking-wide ${source.className}`}
            >
              {source.icon}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm text-fg-primary">{source.label}</p>
              <p className="truncate text-[11px] text-fg-muted">
                {inc.external_id ?? "Operator-created"}
              </p>
            </div>
          </div>
        );
      },
      sortable: true,
    },
    {
      id: "status",
      label: "Status",
      accessor: (inc) => inc.status,
      cell: (inc) => (
        <Badge variant={inc.status as Parameters<typeof Badge>[0]["variant"]}>
          {displayValue(inc.status)}
        </Badge>
      ),
      sortable: true,
    },
    {
      id: "severity",
      label: "Severity",
      accessor: (inc) => inc.severity ?? "",
      cell: (inc) =>
        inc.severity ? (
          <Badge variant={inc.severity}>{inc.severity}</Badge>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
      sortable: true,
    },
    {
      id: "updated_at",
      label: "Last activity",
      accessor: (inc) => inc.updated_at,
      cell: (inc) => {
        const absolute = fmtDate(inc.updated_at);
        return (
          <span
            className="block min-w-[8.5rem] whitespace-nowrap text-sm text-fg-primary"
            title={absolute}
          >
            {fmtRelative(inc.updated_at)}
          </span>
        );
      },
      sortable: true,
    },
  ];
}

function IncidentPhoneCard({
  incident,
  teamName,
}: {
  incident: IncidentResponse;
  teamName: string | null;
}) {
  const source = sourceMeta(incident);
  return (
    <div className="space-y-3">
      <div className="min-w-0">
        <Link
          href={`/dashboard/incidents/detail?id=${incident.id}`}
          className="font-medium text-fg-primary hover:text-accent-text"
        >
          {incident.title}
        </Link>
        <p className="mt-1 text-sm text-fg-muted">{incident.description}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
          {displayValue(incident.status)}
        </Badge>
        {incident.severity ? (
          <Badge variant={incident.severity}>{incident.severity}</Badge>
        ) : null}
        {teamName ? <Badge>{teamName}</Badge> : null}
        <AiSessionBadge incident={incident} />
      </div>
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Source
          </p>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`inline-flex h-6 min-w-6 items-center justify-center rounded-md border px-1 text-[10px] font-semibold uppercase tracking-wide ${source.className}`}
            >
              {source.icon}
            </span>
            <span className="text-fg-primary">{source.label}</span>
          </div>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
            Last activity
          </p>
          <p className="mt-1 text-fg-primary" title={fmtDate(incident.updated_at)}>
            {fmtRelative(incident.updated_at)}
          </p>
        </div>
      </div>
      <p className="text-[11px] text-fg-muted">
        Opened {fmtDate(incident.created_at)}
      </p>
    </div>
  );
}

// Stale-while-revalidate cache: the last successful incidents payload survives
// navigation away/back so returning to the page renders instantly with the
// previous rows while a fresh fetch runs in the background, instead of showing
// a full skeleton on every visit. Refreshed on each successful load.
let incidentsCache: IncidentListResponse | null = null;

// Auto-refresh cadence (ms) while the tab is visible — keeps the AI-session
// badges (and the rest of the list) reasonably live without a dedicated
// org-wide WebSocket broadcast channel. Cheap now that the list endpoint is
// batched; paused entirely when the tab is hidden.
const INCIDENTS_REFRESH_MS = 15_000;
type ConfirmedBulkAction = "resolve" | "reopen" | "delete";

export default function IncidentsPage() {
  const [data, setData] = useState<IncidentListResponse | null>(() => incidentsCache);
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [loading, setLoading] = useState(incidentsCache === null);
  const [showCreate, setShowCreate] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [showCombine, setShowCombine] = useState(false);
  const [managingIncident, setManagingIncident] = useState<IncidentResponse | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [headerActionsOpen, setHeaderActionsOpen] = useState(false);
  const [confirmingAction, setConfirmingAction] =
    useState<ConfirmedBulkAction | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [severityFilter, setSeverityFilter] = useState<string[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string[]>([]);
  const [teamFilter, setTeamFilter] = useState<string[]>([]);
  const [timePreset, setTimePreset] = useState<TimePreset>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const toast = useToast();
  const { user } = useAuth();
  const navigateDashboard = useDashboardNavigation();
  const searchParams = useSearchParams();
  const canManage = user?.role === "admin" || user?.role === "operator";
  // Creating incidents + firing test incidents is admin-only (Operators
  // respond to incidents but don't author them).
  const isAdmin = user?.role === "admin";
  const deferredSearch = useDeferredValue(search);

  // Sprint 61 (Sprint E) — command-palette deep-links.
  // /dashboard/incidents?new=1 opens the create-incident modal on
  // arrival; ?test=1 opens the fire-test modal. The param is
  // consumed once and stripped from the URL so a refresh doesn't
  // re-open the modal.
  useEffect(() => {
    const wantNew = searchParams.get("new");
    const wantTest = searchParams.get("test");
    // Creating + firing test incidents is admin-only — the buttons are hidden
    // for non-admins, so the ?new=1 / ?test=1 deep links (e.g. the Activity /
    // Approvals empty-state hints) must not open the modal for them either.
    if (isAdmin && wantNew === "1") setShowCreate(true);
    if (isAdmin && wantTest === "1") setShowTest(true);
    if (wantNew === "1" || wantTest === "1") {
      const next = new URLSearchParams(searchParams.toString());
      next.delete("new");
      next.delete("test");
      const qs = next.toString();
      navigateDashboard(`/dashboard/incidents${qs ? `?${qs}` : ""}`, { replace: true });
    }
  }, [searchParams, navigateDashboard, isAdmin]);

  const loadMetadata = useCallback(async () => {
    try {
      const [svc, tms] = await Promise.all([
        listServices().catch(() => ({ items: [], total: 0 })),
        listTeams().catch(() => ({ items: [], total: 0 })),
      ]);
      setServices(svc.items);
      setTeams(tms.items);
    } catch {
      setServices([]);
      setTeams([]);
    }
  }, []);

  const loadIncidents = useCallback(async ({ silent = false } = {}) => {
    // Background (auto-refresh / SWR) loads don't flip the spinner so the
    // table doesn't flash; explicit loads do.
    if (!silent) setLoading(true);
    try {
      const presetRange = timeRangeForPreset(timePreset);
      const updatedFrom =
        timePreset === "custom" && customFrom
          ? new Date(customFrom).toISOString()
          : presetRange.from;
      const updatedTo =
        timePreset === "custom" && customTo
          ? new Date(customTo).toISOString()
          : presetRange.to;
      const inc = await listIncidents({
        limit: 200,
        q: deferredSearch.trim() || undefined,
        status: statusFilter.length ? statusFilter : undefined,
        severity: severityFilter.length ? severityFilter : undefined,
        source: sourceFilter.length ? sourceFilter : undefined,
        team_id: teamFilter.length ? teamFilter : undefined,
        updated_from: updatedFrom || undefined,
        updated_to: updatedTo || undefined,
      });
      setData(inc);
      incidentsCache = inc;
    } catch (err) {
      // Background refreshes fail silently — the last good data stays on screen.
      if (!silent) {
        toast.error(err instanceof Error ? err.message : "Failed to load incidents");
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [
    customFrom,
    customTo,
    deferredSearch,
    severityFilter,
    sourceFilter,
    statusFilter,
    teamFilter,
    timePreset,
    toast,
  ]);

  const refresh = useCallback(() => {
    loadMetadata();
    loadIncidents();
  }, [loadIncidents, loadMetadata]);

  useEffect(() => {
    loadMetadata();
  }, [loadMetadata]);

  useEffect(() => {
    loadIncidents();
  }, [loadIncidents]);

  // Live-ish refresh: silently re-fetch on an interval while the tab is
  // visible, and immediately on focus / regaining visibility, so AI-session
  // badges and statuses stay current without a manual refresh. Paused while
  // hidden to avoid needless load.
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) {
        timer = setInterval(() => void loadIncidents({ silent: true }), INCIDENTS_REFRESH_MS);
      }
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        void loadIncidents({ silent: true });
        start();
      } else {
        stop();
      }
    };
    const onFocus = () => {
      if (document.visibilityState === "visible") {
        void loadIncidents({ silent: true });
      }
    };
    if (document.visibilityState === "visible") start();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loadIncidents]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const hasRealIncidents = useMemo(
    () => items.some((incident) => !isSyntheticTestIncident(incident)),
    [items],
  );
  const selectedIncidents = useMemo(
    () => items.filter((incident) => selectedIds.has(incident.id)),
    [items, selectedIds],
  );
  const selectedServiceCount = new Set(
    selectedIncidents.map((incident) => incident.service_id ?? "__global__"),
  ).size;
  const sameServiceSelection =
    selectedIncidents.length > 0 &&
    selectedServiceCount === 1 &&
    selectedIncidents[0]?.service_id != null;
  const canRunLifecycleAction =
    isAdmin || (user?.role === "operator" && sameServiceSelection);
  const allOpenOrInProgress =
    selectedIncidents.length > 0 &&
    selectedIncidents.every(
      (incident) =>
        incident.status === "open" || incident.status === "in_progress",
    );
  const allOpen =
    selectedIncidents.length > 0 &&
    selectedIncidents.every((incident) => incident.status === "open");
  const allResolved =
    selectedIncidents.length > 0 &&
    selectedIncidents.every((incident) => incident.status === "resolved");

  const columns = useMemo(() => buildIncidentColumns(), []);

  const runBulk = useCallback(
    async (
      action: "acknowledge" | "resolve" | "reopen" | "reassign" | "delete",
      userId?: string,
    ) => {
      if (selectedIds.size === 0) return;
      setBulkBusy(true);
      try {
        const ids = Array.from(selectedIds);
        const res = await bulkIncidentAction(action, ids, userId);
        if (res.failed > 0) {
          toast.error(
            `${res.action}: ${res.succeeded} ok, ${res.failed} failed`,
          );
        } else {
          const noun = res.succeeded === 1 ? "incident" : "incidents";
          const message =
            action === "delete"
              ? `${res.succeeded} ${noun} permanently deleted`
              : action === "resolve"
                ? `${res.succeeded} ${noun} marked as resolved`
                : action === "reopen"
                  ? `${res.succeeded} ${noun} reopened`
                  : `${res.action}: ${res.succeeded} updated`;
          toast.success(message);
        }
        setSelectedIds(new Set());
        setActionsOpen(false);
        setConfirmingAction(null);
        await loadIncidents();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setBulkBusy(false);
      }
    },
    [selectedIds, toast, loadIncidents],
  );

  const removeIncident = useCallback(
    async (incident: IncidentResponse) => {
      if (
        !window.confirm(
          `Permanently delete incident "${incident.title}"? This also removes its sessions and operational history. This action cannot be undone.`,
        )
      ) {
        return;
      }
      try {
        await deleteIncident(incident.id);
        setSelectedIds((current) => {
          const next = new Set(current);
          next.delete(incident.id);
          return next;
        });
        toast.success("Incident permanently deleted.");
        await loadIncidents();
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Failed to delete incident",
        );
      }
    },
    [loadIncidents, toast],
  );

  const overview = useMemo(() => {
    const criticalCount = items.filter((item) => item.severity === "critical").length;
    return [
      {
        label: "Matching filters",
        value: String(items.length),
        tone: "text-fg-primary",
      },
      {
        label: "Critical",
        value: String(criticalCount),
        // Alarm red only when something is actually critical — a red zero
        // signals danger for a healthy state.
        tone: criticalCount > 0 ? "text-status-critical" : "text-fg-primary",
      },
      {
        label: "Open",
        value: String(items.filter((item) => item.status === "open").length),
        tone: "text-status-info",
      },
      {
        label: "Ingested",
        value: String(items.filter((item) => sourceMeta(item).key === "ingested").length),
        tone: "text-status-medium",
      },
    ] as const;
  }, [items]);

  // Selection-driven Actions menu. Lives in the filter row (right of the Time
  // filter), always visible and disabled until at least one row is selected —
  // mirroring the Memories page.
  const actionsMenu = canManage ? (
    <div className="relative">
      <Button
        data-testid="incident-actions-trigger"
        className="h-11"
        variant={selectedIds.size > 0 ? "primary" : "secondary"}
        disabled={bulkBusy || selectedIds.size === 0}
        onClick={() => setActionsOpen((open) => !open)}
      >
        Actions <ChevronDown size={13} />
      </Button>
      {actionsOpen ? (
        <>
          <button
            type="button"
            className="fixed inset-0 z-10 cursor-default"
            aria-label="Close incident actions"
            onClick={() => setActionsOpen(false)}
          />
          <div className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border border-border-default bg-bg-panel p-1 shadow-lg">
            <button
              type="button"
              data-testid="incident-action-acknowledge"
              disabled={bulkBusy || !allOpen}
              onClick={() => void runBulk("acknowledge")}
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CheckCircle2 size={14} /> Acknowledge
            </button>
            <button
              type="button"
              data-testid="incident-action-resolve"
              disabled={
                bulkBusy ||
                !canRunLifecycleAction ||
                !allOpenOrInProgress
              }
              title={
                !allOpenOrInProgress
                  ? "Only open or in-progress incidents can be resolved."
                  : !canRunLifecycleAction
                    ? "Operators must select incidents from one service."
                    : undefined
              }
              onClick={() => {
                setConfirmingAction("resolve");
                setActionsOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CheckCircle2 size={14} /> Mark as resolved
            </button>
            <button
              type="button"
              data-testid="incident-action-reopen"
              disabled={
                bulkBusy || !canRunLifecycleAction || !allResolved
              }
              title={
                !allResolved
                  ? "Reopen is available only when every selected incident is resolved."
                  : !canRunLifecycleAction
                    ? "Operators must select incidents from one service."
                    : undefined
              }
              onClick={() => {
                setConfirmingAction("reopen");
                setActionsOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              <RotateCcw size={14} /> Reopen
            </button>
            <button
              type="button"
              disabled={bulkBusy || selectedIds.size < 2}
              onClick={() => {
                setShowCombine(true);
                setActionsOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              Combine
            </button>
            {isAdmin ? (
              <button
                type="button"
                data-testid="incident-action-delete"
                disabled={bulkBusy || selectedIds.size === 0}
                onClick={() => {
                  setConfirmingAction("delete");
                  setActionsOpen(false);
                }}
                className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-status-critical hover:bg-status-critical-bg disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Trash2 size={14} />{" "}
                {selectedIds.size === 1 ? "Delete" : "Delete all"}
              </button>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  ) : undefined;

  return (
    <div>
      <SetupChecklist />
      <div className="mb-6">
        <PageHeader
          title="Incidents"
          subtitle={data ? `${data.total} incidents` : undefined}
          actions={
            <>
              <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}>
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                Refresh
              </Button>
              {isAdmin && (
                <>
                  {hasRealIncidents ? (
                    <div className="relative">
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-label="More incident actions"
                        aria-expanded={headerActionsOpen}
                        onClick={() => setHeaderActionsOpen((open) => !open)}
                      >
                        More <ChevronDown size={13} />
                      </Button>
                      {headerActionsOpen ? (
                        <>
                          <button
                            type="button"
                            className="fixed inset-0 z-10 cursor-default"
                            aria-label="Close incident header actions"
                            onClick={() => setHeaderActionsOpen(false)}
                          />
                          <div className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border border-border-default bg-bg-panel p-1 shadow-lg">
                            <button
                              type="button"
                              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-fg-primary hover:bg-bg-hover"
                              onClick={() => {
                                setHeaderActionsOpen(false);
                                setShowTest(true);
                              }}
                            >
                              Fire Test Incident
                            </button>
                          </div>
                        </>
                      ) : null}
                    </div>
                  ) : (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setShowTest(true)}
                    >
                      Fire Test Incident
                    </Button>
                  )}
                  <Button size="sm" onClick={() => setShowCreate(true)}>
                    <Plus size={14} />
                    New Incident
                  </Button>
                </>
              )}
            </>
          }
        />
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
        {overview.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-border-subtle bg-bg-panel px-3 py-2.5 shadow-sm sm:px-4 sm:py-3"
          >
            <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
              {item.label}
            </p>
            <p className={`mt-1.5 text-xl font-semibold tracking-tight sm:mt-2 sm:text-2xl ${item.tone}`}>
              {item.value}
            </p>
          </div>
        ))}
      </div>

      <IncidentFilterBar
        search={search}
        onSearchChange={setSearch}
        teams={teams}
        teamFilter={teamFilter}
        onTeamFilterChange={setTeamFilter}
        sourceFilter={sourceFilter}
        onSourceFilterChange={setSourceFilter}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        severityFilter={severityFilter}
        onSeverityFilterChange={setSeverityFilter}
        timePreset={timePreset}
        onTimePresetChange={setTimePreset}
        customFrom={customFrom}
        onCustomFromChange={setCustomFrom}
        customTo={customTo}
        onCustomToChange={setCustomTo}
        onClear={() => {
          setSearch("");
          setTeamFilter([]);
          setSourceFilter([]);
          setStatusFilter([]);
          setSeverityFilter([]);
          setTimePreset("all");
          setCustomFrom("");
          setCustomTo("");
        }}
        actionsSlot={actionsMenu}
      />

      {/* Table */}
      {loading && !data ? (
        <TableSkeleton rows={6} columns={5} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title="No incidents yet"
          description={
            isAdmin
              ? "Incidents you create or receive from integrations will appear here. Try a synthetic test incident to walk through the full response loop without touching production."
              : "Incidents you receive from integrations will appear here."
          }
          learnMoreHref="https://github.com/SpicyDaemon/OpsMender-AI/tree/main/docs/wiki/operator-guide.md"
          learnMoreLabel="Operator guide"
          action={
            isAdmin ? (
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button size="sm" variant="secondary" onClick={() => setShowTest(true)}>
                  Fire Test Incident
                </Button>
                <Button size="sm" onClick={() => setShowCreate(true)}>
                  <Plus size={14} />
                  New Incident
                </Button>
              </div>
            ) : undefined
          }
        />
      ) : (
        <DataTable
          rows={items}
          columns={columns}
          rowKey={(inc) => inc.id}
          phoneLayout={(inc) => (
            <IncidentPhoneCard
              incident={inc}
              teamName={inc.team_name ?? null}
            />
          )}
          storageKey="opsmender:incidents-table"
          hideToolbar
          selectable={canManage}
          selectedKeys={selectedIds}
          onSelectionChange={setSelectedIds}
          rowActions={canManage ? (inc) => (
            <div className="flex items-center gap-1">
              <Button size="sm" variant="secondary" onClick={() => setManagingIncident(inc)}>
                Manage
              </Button>
              {isAdmin && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void removeIncident(inc)}
                  title={`Delete incident ${inc.title}`}
                  aria-label={`Delete incident ${inc.title}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          ) : undefined}
        />
      )}

      {/* Create modal */}
      <CreateIncidentModal
        open={showCreate}
        services={services}
        onClose={() => setShowCreate(false)}
        onCreated={(result) => {
          setShowCreate(false);
          const opts = {
            label: "Open incident",
            href: `/dashboard/incidents/detail?id=${result.id}`,
          };
          if (result.auto_start_status === "failed") {
            toast.warning(result.auto_start_message, opts);
          } else {
            toast.success(result.auto_start_message, opts);
          }
          loadIncidents();
        }}
      />
      <ManageIncidentModal
        open={Boolean(managingIncident)}
        incident={managingIncident}
        services={services}
        teams={teams}
        onClose={() => setManagingIncident(null)}
        onUpdated={() => {
          setManagingIncident(null);
          loadIncidents();
        }}
      />
      <FireTestIncidentModal
        open={showTest}
        services={services}
        onClose={() => setShowTest(false)}
        onCreated={(result) => {
          setShowTest(false);
          const opts = {
            label: "Open incident",
            href: `/dashboard/incidents/detail?id=${result.incident.id}`,
          };
          if (result.auto_start_status === "failed") {
            toast.warning(result.message, opts);
          } else {
            toast.success(result.message, opts);
          }
          loadIncidents();
        }}
      />
      <CombineIncidentsModal
        open={showCombine}
        incidents={items.filter((inc) => selectedIds.has(inc.id))}
        onClose={() => setShowCombine(false)}
        onCombined={(primaryId, mergedCount) => {
          setShowCombine(false);
          setSelectedIds(new Set());
          toast.success(`Combined ${mergedCount} incident${mergedCount === 1 ? "" : "s"}.`, {
            label: "Open primary",
            href: `/dashboard/incidents/detail?id=${primaryId}`,
          });
          loadIncidents();
        }}
      />
      <Modal
        open={confirmingAction !== null}
        onClose={() => setConfirmingAction(null)}
        title={
          confirmingAction === "delete"
            ? selectedIds.size === 1
              ? "Delete incident?"
              : "Delete incidents?"
            : confirmingAction === "reopen"
              ? selectedIds.size === 1
                ? "Reopen incident?"
                : "Reopen incidents?"
              : selectedIds.size === 1
                ? "Mark incident as resolved?"
                : "Mark incidents as resolved?"
        }
      >
        <p className="text-sm text-fg-secondary">
          {confirmingAction === "delete"
            ? `Are you sure you want to permanently delete ${selectedIds.size} ${
                selectedIds.size === 1 ? "incident" : "incidents"
              }? This removes their sessions and operational history and cannot be undone.`
            : confirmingAction === "reopen"
              ? `Are you sure you want to reopen ${selectedIds.size} ${
                  selectedIds.size === 1 ? "incident" : "incidents"
                }?`
              : `Are you sure you want to mark ${selectedIds.size} ${
                  selectedIds.size === 1 ? "incident" : "incidents"
                } as resolved? Any running AI sessions will be stopped.`}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => setConfirmingAction(null)}
          >
            Cancel
          </Button>
          <Button
            data-testid="confirm-incident-bulk-action"
            variant={confirmingAction === "delete" ? "danger" : "primary"}
            disabled={bulkBusy || confirmingAction === null}
            onClick={() => {
              if (confirmingAction) void runBulk(confirmingAction);
            }}
          >
            {confirmingAction === "delete"
              ? selectedIds.size === 1
                ? "Delete"
                : "Delete all"
              : confirmingAction === "reopen"
                ? "Reopen"
                : "Mark as resolved"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact filters + management modal
// ---------------------------------------------------------------------------

function IncidentFilterBar({
  search,
  onSearchChange,
  teams,
  teamFilter,
  onTeamFilterChange,
  sourceFilter,
  onSourceFilterChange,
  statusFilter,
  onStatusFilterChange,
  severityFilter,
  onSeverityFilterChange,
  timePreset,
  onTimePresetChange,
  customFrom,
  onCustomFromChange,
  customTo,
  onCustomToChange,
  onClear,
  actionsSlot,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  teams: TeamResponse[];
  teamFilter: string[];
  onTeamFilterChange: (value: string[]) => void;
  sourceFilter: string[];
  onSourceFilterChange: (value: string[]) => void;
  statusFilter: string[];
  onStatusFilterChange: (value: string[]) => void;
  severityFilter: string[];
  onSeverityFilterChange: (value: string[]) => void;
  timePreset: TimePreset;
  onTimePresetChange: (value: TimePreset) => void;
  customFrom: string;
  onCustomFromChange: (value: string) => void;
  customTo: string;
  onCustomToChange: (value: string) => void;
  onClear: () => void;
  /** Selection-driven Actions menu, rendered right of the Time filter. */
  actionsSlot?: ReactNode;
}) {
  const toggle = (arr: string[], value: string) =>
    arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
  const hasFilters = Boolean(
    search ||
      teamFilter.length ||
      sourceFilter.length ||
      statusFilter.length ||
      severityFilter.length ||
      timePreset !== "all" ||
      customFrom ||
      customTo,
  );
  return (
    <div className="mb-4 rounded-xl border border-border-subtle bg-bg-panel/95 p-3 shadow-sm">
      {/* Flex row matching the DataTable filterBar (MCP Servers): search grows
          to fill, filters keep their natural width at a single consistent gap,
          and Clear is pushed to the right. Wraps on narrow screens. */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
          />
          <Input
            aria-label="Search incidents"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search incidents..."
            className="h-11 pl-9"
          />
        </div>
        <FilterDropdown
          label="teams"
          options={teams.map((team) => ({ value: team.id, label: team.name }))}
          selected={teamFilter}
          onToggle={(v) => onTeamFilterChange(toggle(teamFilter, v))}
        />
        <FilterDropdown
          label="sources"
          options={SOURCE_OPTIONS.filter((o) => o.value).map((o) => ({
            value: o.value,
            label: o.label,
          }))}
          selected={sourceFilter}
          onToggle={(v) => onSourceFilterChange(toggle(sourceFilter, v))}
        />
        <FilterDropdown
          label="statuses"
          options={STATUS_OPTIONS.filter((o) => o.value).map((o) => ({
            value: o.value,
            label: o.label,
          }))}
          selected={statusFilter}
          onToggle={(v) => onStatusFilterChange(toggle(statusFilter, v))}
        />
        <FilterDropdown
          label="severities"
          options={SEVERITY_OPTIONS.filter((o) => o.value).map((o) => ({
            value: o.value,
            label: o.label,
          }))}
          selected={severityFilter}
          onToggle={(v) => onSeverityFilterChange(toggle(severityFilter, v))}
        />
        <IncidentTimeFilter
          preset={timePreset}
          onPresetChange={onTimePresetChange}
          customFrom={customFrom}
          onCustomFromChange={onCustomFromChange}
          customTo={customTo}
          onCustomToChange={onCustomToChange}
        />
        {actionsSlot}
        <div className="ml-auto flex items-center gap-2">
          {hasFilters ? (
            <Button variant="ghost" size="sm" onClick={onClear} className="h-11">
              <X size={14} />
              Clear
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function IncidentTimeFilter({
  preset,
  onPresetChange,
  customFrom,
  onCustomFromChange,
  customTo,
  onCustomToChange,
}: {
  preset: TimePreset;
  onPresetChange: (value: TimePreset) => void;
  customFrom: string;
  onCustomFromChange: (value: string) => void;
  customTo: string;
  onCustomToChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"relative" | "custom">(
    preset === "custom" ? "custom" : "relative",
  );
  const label =
    preset === "custom"
      ? customFrom || customTo
        ? `${customFrom || "Start"} to ${customTo || "Now"}`
        : "Custom time"
      : TIME_PRESETS.find((option) => option.value === preset)?.label ?? "All time";

  return (
    <div className="relative w-48">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex h-11 w-full items-center justify-between gap-3 rounded-md border border-border-default bg-bg-surface px-4 text-left text-sm text-fg-primary transition hover:border-border-strong focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      >
        <span className="inline-flex min-w-0 items-center gap-2">
          <Clock size={14} className="shrink-0 text-fg-muted" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
            Time
          </span>
          <span className="truncate">{label}</span>
        </span>
        <ChevronDown size={14} className="shrink-0 text-fg-muted" />
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-2 w-[23rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border-default bg-bg-panel shadow-xl">
            <div className="grid grid-cols-2 border-b border-border-subtle text-sm">
              <button
                type="button"
                onClick={() => setTab("relative")}
                className={`px-4 py-3 font-medium ${tab === "relative" ? "border-b-2 border-accent text-fg-primary" : "text-fg-muted"}`}
              >
                Relative
              </button>
              <button
                type="button"
                onClick={() => setTab("custom")}
                className={`px-4 py-3 font-medium ${tab === "custom" ? "border-b-2 border-accent text-fg-primary" : "text-fg-muted"}`}
              >
                Custom
              </button>
            </div>
            {tab === "relative" ? (
              <div className="max-h-80 overflow-y-auto py-2">
                {TIME_PRESETS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => {
                      onPresetChange(option.value);
                      setOpen(false);
                    }}
                    className={`block w-full px-4 py-2.5 text-left text-sm hover:bg-bg-hover ${preset === option.value ? "text-accent-text" : "text-fg-primary"}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            ) : (
              <div className="grid gap-4 p-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label htmlFor="incident-time-from">From</Label>
                    <Input
                      id="incident-time-from"
                      type="datetime-local"
                      value={customFrom}
                      onChange={(e) => onCustomFromChange(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor="incident-time-to">To</Label>
                    <Input
                      id="incident-time-to"
                      type="datetime-local"
                      value={customTo}
                      onChange={(e) => onCustomToChange(e.target.value)}
                    />
                  </div>
                </div>
                <Button
                  onClick={() => {
                    onPresetChange("custom");
                    setOpen(false);
                  }}
                >
                  Apply
                </Button>
              </div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}

function ManageIncidentModal({
  open,
  incident,
  services,
  teams,
  onClose,
  onUpdated,
}: {
  open: boolean;
  incident: IncidentResponse | null;
  services: ServiceResponse[];
  teams: TeamResponse[];
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [status, setStatus] = useState<IncidentStatus>("open");
  const [severity, setSeverity] = useState<Severity>("high");
  const [serviceId, setServiceId] = useState("");
  const [handoffReason, setHandoffReason] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!incident) return;
    setStatus(incident.status);
    setSeverity(incident.severity ?? "high");
    setServiceId(incident.service_id ?? "");
    setHandoffReason("");
    setError("");
  }, [incident]);

  const teamById = useMemo(() => new Map(teams.map((team) => [team.id, team.name])), [teams]);
  const serviceChanged = Boolean(incident && serviceId !== (incident.service_id ?? ""));

  async function handleSubmit() {
    if (!incident) return;
    setError("");
    setLoading(true);
    try {
      await updateIncident(incident.id, {
        status,
        severity,
        service_id: serviceId || null,
        service_id_set: true,
        handoff_reason: handoffReason || undefined,
      });
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update incident");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Manage Incident">
      <div className="space-y-4">
        {incident ? (
          <div className="rounded-lg border border-border-subtle bg-bg-elevated px-4 py-3">
            <p className="font-medium text-fg-primary">{incident.title}</p>
            <p className="mt-1 text-sm text-fg-muted">
              Updating the service moves ownership to that service&apos;s team and restarts paging for its escalation chain when one is configured.
            </p>
          </div>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="incident-status">Status</Label>
            <Select
              id="incident-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as IncidentStatus)}
            >
              {STATUS_OPTIONS.filter((option) => option.value).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="incident-severity">Severity</Label>
            <Select
              id="incident-severity"
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Severity)}
            >
              {SEVERITY_OPTIONS.filter((option) => option.value).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="incident-service">Service / Team</Label>
          <Select
            id="incident-service"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
          >
            <option value="">No linked service</option>
            {services.map((service) => (
              <option key={service.id} value={service.id}>
                {service.name} {teamById.get(service.team_id) ? `- ${teamById.get(service.team_id)}` : ""}
              </option>
            ))}
          </Select>
        </div>
        {serviceChanged ? (
          <div>
            <Label htmlFor="incident-handoff-reason">Handoff note (optional)</Label>
            <Textarea
              id="incident-handoff-reason"
              rows={3}
              value={handoffReason}
              onChange={(e) => setHandoffReason(e.target.value)}
              placeholder="Why is this moving to another service/team?"
            />
          </div>
        ) : null}
        {error ? <FormError message={error} /> : null}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={loading} disabled={!incident}>
            Save changes
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Create incident modal
// ---------------------------------------------------------------------------

function CreateIncidentModal({
  open,
  services,
  onClose,
  onCreated,
}: {
  open: boolean;
  services: ServiceResponse[];
  onClose: () => void;
  onCreated: (result: IncidentCreateResponse) => void;
}) {
  const [form, setForm] = useState<IncidentCreate>({
    title: "",
    description: "",
    severity: undefined,
    service_id: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function reset() {
    setForm({
      title: "",
      description: "",
      severity: undefined,
      service_id: "",
    });
    setError("");
  }

  async function handleSubmit() {
    setError("");
    setLoading(true);
    try {
      const result = await createIncident(form);
      reset();
      onCreated(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { reset(); onClose(); }}
      title="Create Incident"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="ci-service">Service</Label>
          <Select
            id="ci-service"
            value={form.service_id ?? ""}
            onChange={(e) =>
              setForm((current) => ({
                ...current,
                service_id: e.target.value,
              }))
            }
          >
            <option value="">Select an active service</option>
            {services
              .filter((service) => service.is_active)
              .map((service) => (
                <option key={service.id} value={service.id}>
                  {service.name}
                </option>
              ))}
          </Select>
          {services.some((service) => service.is_active) ? (
            <p className="mt-1 text-xs text-fg-muted">
              Manual incidents must be linked to an active service.
            </p>
          ) : (
            <p className="mt-1 text-xs text-status-high">
              Create an active service before creating a manual incident.
            </p>
          )}
        </div>
        <div>
          <Label htmlFor="ci-title">Title</Label>
          <Input
            id="ci-title"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Database cluster unreachable"
          />
        </div>
        <div>
          <Label htmlFor="ci-desc">Description</Label>
          <Textarea
            id="ci-desc"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Describe the incident in detail..."
            rows={4}
          />
        </div>
        <div>
          <Label htmlFor="ci-sev">Severity</Label>
          <Select
            id="ci-sev"
            value={form.severity ?? ""}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                severity: (e.target.value as Severity) || undefined,
              }))
            }
          >
            <option value="">None</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </Select>
        </div>
        {error && <FormError message={error} />}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={() => { reset(); onClose(); }}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            loading={loading}
            disabled={!form.title || !form.description || !form.service_id}
          >
            Create
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function createSyntheticPayload(serviceName?: string): Pick<
  IncidentCreate,
  "title" | "description" | "severity" | "external_id" | "external_source"
> {
  const scope = serviceName ? ` for ${serviceName}` : "";
  return {
    title: `TEST · synthetic alert${scope}`,
    description:
      `Synthetic alert fired from the Incidents page${scope}. ` +
      "Use this to verify ingestion, paging, sessions, and operator workflow end to end.",
    severity: "high",
    external_id: `test-${Date.now()}`,
    external_source: "opsmender-test",
  };
}

function FireTestIncidentModal({
  open,
  services,
  onClose,
  onCreated,
}: {
  open: boolean;
  services: ServiceResponse[];
  onClose: () => void;
  onCreated: (result: FireTestIncidentResponse) => void;
}) {
  const [serviceId, setServiceId] = useState("");
  const [form, setForm] = useState<IncidentCreate>(() => createSyntheticPayload());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    const selectedService = services.find((service) => service.id === serviceId);
    const base = createSyntheticPayload(selectedService?.name);
    setForm({
      ...base,
      service_id: serviceId || undefined,
    });
  }, [open, serviceId, services]);

  function reset() {
    setServiceId("");
    setForm(createSyntheticPayload());
    setError("");
  }

  async function handleSubmit() {
    setError("");
    setLoading(true);
    try {
      const result = await fireTestIncident({
        service_id: serviceId || undefined,
      });
      reset();
      onCreated(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fire test incident");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Fire Test Incident"
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-status-high-border bg-status-high-bg/40 px-4 py-3 text-sm text-fg-primary">
          This creates a synthetic high-severity incident so you can verify
          ingestion, paging, and operator flow. An AI session will only
          auto-start if the resolved autonomy tier is T0 and auto-start is
          allowed by policy.
        </div>

        <div>
          <Label htmlFor="test-service">Service (optional)</Label>
          <Select
            id="test-service"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
          >
            <option value="">No linked service</option>
            {services.map((service) => (
              <option key={service.id} value={service.id}>
                {service.name}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="test-title">Synthetic Payload</Label>
          <Textarea
            id="test-title"
            rows={6}
            value={JSON.stringify(
              {
                title: form.title,
                description: form.description,
                severity: form.severity,
                source: form.external_source,
                service_id: form.service_id ?? null,
              },
              null,
              2,
            )}
            readOnly
            className="font-mono text-xs"
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="secondary"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={loading}>
            Fire Test Incident
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function CombineIncidentsModal({
  open,
  incidents,
  onClose,
  onCombined,
}: {
  open: boolean;
  incidents: IncidentResponse[];
  onClose: () => void;
  onCombined: (primaryId: string, mergedCount: number) => void;
}) {
  const [primaryId, setPrimaryId] = useState<string>("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Default the primary to the oldest selected incident (the original report)
  // whenever the selection changes.
  useEffect(() => {
    if (!open) return;
    const oldest = [...incidents].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    )[0];
    setPrimaryId(oldest?.id ?? "");
    setNote("");
    setError("");
  }, [open, incidents]);

  async function handleSubmit() {
    if (!primaryId) {
      setError("Pick a primary incident to keep.");
      return;
    }
    const secondaryIds = incidents.map((i) => i.id).filter((id) => id !== primaryId);
    if (secondaryIds.length === 0) {
      setError("Select at least one other incident to combine in.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      await combineIncidents(primaryId, secondaryIds, note.trim() || undefined);
      onCombined(primaryId, secondaryIds.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to combine incidents");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Combine incidents">
      <div className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Pick the <strong>primary</strong> incident to keep. The others are folded
          into it: their comments move over, their AI sessions stop, and they move
          to a <code>merged</code> state pointing at the primary (nothing is deleted).
        </p>

        <div className="space-y-2">
          {incidents.map((inc) => (
            <label
              key={inc.id}
              className="flex cursor-pointer items-start gap-3 rounded-lg border border-border-subtle p-3 hover:border-border-strong"
            >
              <input
                type="radio"
                name="combine-primary"
                className="mt-1"
                checked={primaryId === inc.id}
                onChange={() => setPrimaryId(inc.id)}
              />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-fg-primary">{inc.title}</span>
                  {primaryId === inc.id ? (
                    <Badge variant="info">Primary</Badge>
                  ) : (
                    <Badge variant="default">Merge in</Badge>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] text-fg-muted">
                  {displayValue(inc.status)} • opened {fmtDate(inc.created_at)}
                </p>
              </div>
            </label>
          ))}
        </div>

        <div>
          <Label htmlFor="combine-note">Note (optional)</Label>
          <Textarea
            id="combine-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why these are the same incident…"
            rows={2}
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={loading}>
            Combine {Math.max(0, incidents.length - 1)} into primary
          </Button>
        </div>
      </div>
    </Modal>
  );
}
