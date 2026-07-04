"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { IncidentCommandStrip } from "@/components/incidents/IncidentCommandStrip";
import { IncidentContextRail } from "@/components/incidents/IncidentContextRail";
import { IncidentTimeline } from "@/components/incidents/IncidentTimeline";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CalendarClock,
  CalendarX,
  MessageSquare,
  Play,
  Radar,
  ShieldAlert,
  TimerReset,
} from "lucide-react";
import {
  createSession,
  getIncident,
  getIncidentPaging,
  getIncidentTimeline,
  listAgentTeamProfiles,
  listIncidentSessions,
  listProviders,
  listUsers,
  listWorkflowProfiles,
} from "@/lib/api";
import type {
  AgentTeamProfileResponse,
  IncidentPagingPanelResponse,
  IncidentResponse,
  IncidentTimelineItemResponse,
  ProviderModelsResponse,
  SessionCreate,
  SessionResponse,
  UserResponse,
  WorkflowProfileResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { useAuth } from "@/context/auth";
import { acknowledgedByName, responderDisplay } from "@/lib/responder";
import { Modal } from "@/components/ui/Modal";
import { DetailSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { IncidentSessionSidecar } from "@/components/sessions/IncidentSessionSidecar";
import { formatDateTime, formatRelative } from "@/lib/formatDate";

function fmtDate(iso: string) {
  return formatDateTime(iso);
}

const fmtRelative = formatRelative;

function sourceMeta(incident: IncidentResponse) {
  if (!incident.external_source) {
    return {
      label: "Manual",
      detail: "Operator-created incident",
      tone: "bg-status-info-bg text-status-info border-status-info-border",
    };
  }
  if (incident.external_source === "opsmender-test") {
    return {
      label: "Test",
      detail: "Synthetic alert",
      tone: "bg-status-high-bg text-status-high border-status-high-border",
    };
  }
  const label = incident.external_source
    .replace(/^auto:/, "")
    .replace(/_/g, " ")
    .split(":")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" / ");
  return {
    label: label || "Ingested",
    detail: incident.external_id ?? "Inbound alert",
    tone: "bg-status-medium-bg text-status-medium border-status-medium-border",
  };
}

function sessionStatusSummary(sessions: SessionResponse[]) {
  if (sessions.some((session) => session.status === "queued")) {
    return { label: "Waiting for AI capacity", variant: "queued" as const };
  }
  if (sessions.some((session) => session.status === "awaiting_approval")) {
    return { label: "Awaiting approval", variant: "awaiting_approval" as const };
  }
  if (sessions.some((session) => session.status === "active")) {
    return { label: "Active session", variant: "active" as const };
  }
  if (sessions.some((session) => session.status === "failed")) {
    return { label: "Needs attention", variant: "failed" as const };
  }
  if (sessions.some((session) => session.status === "timed_out")) {
    return { label: "Timed out", variant: "timed_out" as const };
  }
  if (sessions.length > 0) {
    return { label: "History available", variant: "completed" as const };
  }
  return { label: "No sessions yet", variant: "default" as const };
}

export default function IncidentDetailPage() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <IncidentDetailContent />
    </Suspense>
  );
}

/**
 * Read-only incident view for Viewers: summary + lifecycle status only. No AI
 * session content, tool activity, paging, approvals, or action buttons.
 */
function ViewerIncidentView({ incident }: { incident: IncidentResponse }) {
  const fmt = (iso: string) => formatDateTime(iso);
  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href="/dashboard/incidents"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg-primary"
      >
        <ArrowLeft size={14} /> Incidents
      </Link>

      <div className="rounded-xl border border-border-subtle bg-bg-panel p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h1 className="text-lg font-semibold text-fg-primary">{incident.title}</h1>
          <div className="flex items-center gap-2">
            <Badge>{incident.status}</Badge>
            {incident.severity && <Badge variant="default">{incident.severity}</Badge>}
          </div>
        </div>
        {incident.description && (
          <p className="mt-3 whitespace-pre-wrap text-sm text-fg-secondary">
            {incident.description}
          </p>
        )}
        <dl className="mt-5 grid grid-cols-1 gap-4 border-t border-border-subtle pt-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">Status</dt>
            <dd className="mt-1 text-fg-primary">{incident.status}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">Responder</dt>
            <dd className="mt-1 text-fg-primary">{responderDisplay(incident).text}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">Source</dt>
            <dd className="mt-1 text-fg-primary">{incident.external_source || "manual"}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">Created</dt>
            <dd className="mt-1 text-fg-primary">{fmt(incident.created_at)}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">Last updated</dt>
            <dd className="mt-1 text-fg-primary">{fmt(incident.updated_at)}</dd>
          </div>
        </dl>
      </div>
      <p className="mt-3 text-center text-xs text-fg-muted">
        You have read-only access. Remediation details and AI sessions are visible to operators and admins.
      </p>
    </div>
  );
}

function IncidentDetailContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const fromSurface = (searchParams.get("from") ?? "").toLowerCase();
  const cameFromChat =
    fromSurface === "slack" || fromSurface === "teams" || fromSurface === "discord";
  const router = useRouter();
  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [pagingPanel, setPagingPanel] =
    useState<IncidentPagingPanelResponse | null>(null);
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [sessionsError, setSessionsError] = useState("");
  const [timeline, setTimeline] = useState<IncidentTimelineItemResponse[]>([]);
  const [timelineError, setTimelineError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showSession, setShowSession] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState("");
  const toast = useToast();
  const { user } = useAuth();
  // Viewers get a read-only summary: no AI sessions, tool activity, paging, or
  // lifecycle actions (those endpoints are admin/operator-only on the backend).
  const isViewer = user?.role === "viewer";

  const reload = useCallback(async () => {
    if (!id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setSessionsError("");
    setTimelineError("");
    try {
      const incidentRes = await getIncident(id);
      setIncident(incidentRes);
      if (isViewer) {
        // Skip operator/admin-only fetches (sessions, timeline, paging, users).
        setLoading(false);
        return;
      }
      Promise.all([
        getIncidentPaging(id).catch(() => null),
        listUsers().catch(() => ({ items: [], total: 0 })),
      ])
        .then(([p, userList]) => {
          setPagingPanel(p);
          setUsers(userList.items);
        })
        .catch(() => setPagingPanel(null));
    } catch (err) {
      setIncident(null);
      setSessions([]);
      setUsers([]);
      toast.error(err instanceof Error ? err.message : "Failed to load incident");
      setLoading(false);
      return;
    }

    try {
      const [sessionsRes, timelineRes] = await Promise.all([
        listIncidentSessions(id),
        getIncidentTimeline(id),
      ]);
      setSessions(sessionsRes.items);
      setTimeline(timelineRes.items);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load incident activity";
      setSessions([]);
      setTimeline([]);
      setSessionsError(message);
      setTimelineError(message);
      toast.warning(`Incident activity is temporarily unavailable: ${message}`);
    } finally {
      setLoading(false);
    }
  }, [id, toast, isViewer]);

  // Lightweight refresh of sessions + timeline (e.g. after posting a comment)
  // without flipping the whole page into a loading state.
  const reloadActivity = useCallback(async () => {
    if (!id || isViewer) return;
    try {
      const [sessionsRes, timelineRes] = await Promise.all([
        listIncidentSessions(id),
        getIncidentTimeline(id),
      ]);
      setSessions(sessionsRes.items);
      setTimeline(timelineRes.items);
    } catch {
      // Best-effort; keep the existing view if the refresh fails.
    }
  }, [id, isViewer]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await reload();
    })();
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const source = useMemo(() => (incident ? sourceMeta(incident) : null), [incident]);
  const sessionSummary = useMemo(() => sessionStatusSummary(sessions), [sessions]);
  const ownerLabel = useMemo(() => {
    const assignedTo = pagingPanel?.assignment?.assigned_to;
    if (!assignedTo) return null;
    const owner = users.find((candidate) => candidate.id === assignedTo);
    // Fallback identity for users that were deleted after acting on the incident.
    if (!owner) return `Deleted user ${assignedTo.slice(0, 8)}`;
    return owner.username || owner.email;
  }, [pagingPanel, users]);

  if (loading) return <DetailSkeleton />;
  if (!id) return <p className="text-status-critical">Missing incident id.</p>;
  if (!incident) return <p className="text-status-critical">Incident not found.</p>;

  if (isViewer) return <ViewerIncidentView incident={incident} />;

  const acknowledgedByDetail = acknowledgedByName(incident);

  return (
    <div className="mx-auto max-w-7xl">
      {/* Sprint A Step 1: sticky command strip surfaces the lifecycle actions
          (Acknowledge / Take / Start session / Resolve / Postmortem) at the
          top of the screen, independent of the body scroll position. */}
      <IncidentCommandStrip
        incident={incident}
        assignment={pagingPanel?.assignment ?? null}
        onStartSession={() => setShowSession(true)}
        onChanged={reload}
        ownerLabel={ownerLabel}
      />

      {incident.merged_into_incident_id && (
        <div className="mb-4 rounded-lg border border-status-info-border bg-status-info-bg px-4 py-3 text-sm text-status-info">
          This incident was <strong>combined</strong> into another incident.{" "}
          <Link
            href={`/dashboard/incidents/detail?id=${incident.merged_into_incident_id}`}
            className="font-medium underline hover:no-underline"
          >
            Open the primary incident →
          </Link>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
          Responder
        </span>
        <span
          className={`rounded-full border px-2.5 py-0.5 font-medium ${
            responderDisplay(incident).tone === "ok"
              ? "border-status-low-border bg-status-low-bg text-status-low"
              : responderDisplay(incident).tone === "warn"
                ? "border-status-medium-border bg-status-medium-bg text-status-medium"
                : "border-border-subtle bg-bg-elevated text-fg-muted"
          }`}
        >
          {responderDisplay(incident).text}
        </span>
        {acknowledgedByDetail && (
          <span className="text-xs text-fg-muted">Acknowledged by {acknowledgedByDetail}</span>
        )}
      </div>

      <div className={`grid gap-6 ${activeSessionId ? "xl:grid-cols-[minmax(0,1.7fr)_minmax(360px,0.95fr)]" : ""}`}>
        <div className="min-w-0">
          {/* Back */}
          <Link
            href="/dashboard/incidents"
            className="mb-6 inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg-primary"
          >
            <ArrowLeft size={14} /> Incidents
          </Link>

          {cameFromChat && (
            <div
              data-testid="from-chat-breadcrumb"
              className="mb-4 flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-fg-primary"
            >
              <MessageSquare size={16} className="shrink-0 text-accent-text" />
              <span>
                You opened this incident from{" "}
                <span className="font-medium capitalize">{fromSurface}</span>.
                Anything you do here syncs back to the chat surface.
              </span>
            </div>
          )}

          {pagingPanel?.suppressed_by_maintenance_window && (
            <div className="mb-4 flex items-start gap-3 rounded-lg border border-status-warning/40 bg-status-warning/10 px-4 py-3 text-sm">
              <CalendarX
                size={18}
                className="mt-0.5 shrink-0 text-status-warning"
              />
              <div>
                <div className="font-medium text-fg-primary">
                  Paging suppressed by maintenance window
                </div>
                <div className="text-xs text-fg-secondary">
                  <span className="font-medium">
                    {pagingPanel.suppressed_by_maintenance_window.name}
                  </span>{" "}
                  ({pagingPanel.suppressed_by_maintenance_window.scope_type}){" "}
                  · {formatDateTime(
                    pagingPanel.suppressed_by_maintenance_window.starts_at,
                  )}
                  {" → "}
                  {formatDateTime(
                    pagingPanel.suppressed_by_maintenance_window.ends_at,
                  )}
                  . No one was paged for this incident.
                </div>
              </div>
            </div>
          )}

          <div className="mb-6 overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <div className="border-b border-border-subtle bg-[linear-gradient(135deg,rgba(59,130,246,0.12),transparent_45%),linear-gradient(225deg,rgba(234,179,8,0.10),transparent_50%)] px-4 py-4 sm:px-6 sm:py-6">
              <div className="flex flex-col gap-4 sm:gap-6 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                  {/* Status/severity live in the sticky command strip above
                      (persistent on scroll); the session posture is in the
                      "Session history" metric below — so the hero shows only
                      the alert source to avoid repeating the same pills. */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${source?.tone}`}>
                      <Radar size={12} />
                      {source?.label}
                    </span>
                  </div>
                  <h1 className="mt-3 text-xl font-semibold tracking-tight text-fg-primary sm:mt-4 sm:text-3xl">
                    {incident.title}
                  </h1>
                  <p className="mt-3 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-fg-secondary">
                    {incident.description}
                  </p>
                  <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-fg-muted">
                    <span className="font-mono">incident {incident.id.slice(0, 8)}…</span>
                    <span>Opened {fmtDate(incident.created_at)}</span>
                    <span>Updated {fmtRelative(incident.updated_at)}</span>
                    {incident.external_id && <span>Fingerprint {incident.external_id}</span>}
                  </div>
                </div>

                <div className="flex w-full flex-col gap-3 lg:min-w-[240px] lg:w-auto">
                  {/* "Start Session" lives on the command strip above so the
                      action stays one click away regardless of scroll. */}
                  <div className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">
                      Quick View
                    </p>
                    <div className="mt-3 space-y-3 text-sm">
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-fg-muted">Source</span>
                        <span className="text-right text-fg-primary">{source?.detail}</span>
                      </div>
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-fg-muted">Sessions</span>
                        <span className="text-fg-primary">{sessions.length}</span>
                      </div>
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-fg-muted">Most recent activity</span>
                        <span className="text-right text-fg-primary">{fmtRelative(incident.updated_at)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-3 px-4 py-4 sm:px-6 md:grid-cols-3">
              <DetailMetric
                icon={CalendarClock}
                label="Created"
                value={fmtDate(incident.created_at)}
                hint="Initial incident open time"
              />
              <DetailMetric
                icon={TimerReset}
                label="Last updated"
                value={fmtDate(incident.updated_at)}
                hint={fmtRelative(incident.updated_at)}
              />
              <DetailMetric
                icon={ShieldAlert}
                label="Session history"
                value={sessionSummary.label}
                hint={sessions.length > 0 ? `${sessions.length} session${sessions.length === 1 ? "" : "s"} recorded` : "Ready for first response run"}
              />
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
            <IncidentTimeline
              items={timeline}
              error={timelineError || sessionsError}
              activeSessionId={activeSessionId}
              onSelectSession={setActiveSessionId}
              onStartSession={() => setShowSession(true)}
              incidentId={id}
              canComment={!isViewer}
              onCommentAdded={reloadActivity}
            />

            {/* Sprint 57 Step 2: unified right-rail panel surfaces severity,
                status, service, team, owner, escalation step, AI tier, and
                pending approvals in one place. Replaces the previous
                Context + Suggested-Next-Step cards. */}
            <IncidentContextRail
              incident={incident}
              pagingPanel={pagingPanel}
              sessions={sessions}
            />
          </div>
        </div>
        {activeSessionId ? (
          <IncidentSessionSidecar
            sessionId={activeSessionId}
            onClose={() => setActiveSessionId("")}
          />
        ) : null}
      </div>

      {/* Start session modal */}
      <StartSessionModal
        open={showSession}
        onClose={() => setShowSession(false)}
        incidentId={incident.id}
        isAcknowledged={!!pagingPanel?.assignment?.assigned_to}
        onStarted={(session) => {
          setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
          setActiveSessionId(session.id);
          setShowSession(false);
          if (session.capacity_warning) {
            toast.warning(session.capacity_warning);
          } else if (session.status === "queued") {
            toast.info("AI session queued until model capacity becomes available.");
          }
          router.prefetch(`/dashboard/sessions/detail?id=${session.id}`);
        }}
      />
    </div>
  );
}

function DetailMetric({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof CalendarClock;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
      <div className="flex items-center gap-2 text-fg-muted">
        <Icon size={14} />
        <span className="text-[11px] font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="mt-3 text-sm font-semibold text-fg-primary">{value}</p>
      <p className="mt-1 text-xs text-fg-secondary">{hint}</p>
    </div>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wide text-fg-muted">{label}</p>
      <div>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Start session modal
// ---------------------------------------------------------------------------

function StartSessionModal({
  open,
  onClose,
  incidentId,
  isAcknowledged,
  onStarted,
}: {
  open: boolean;
  onClose: () => void;
  incidentId: string;
  isAcknowledged: boolean;
  onStarted: (session: SessionResponse) => void;
}) {
  const [providers, setProviders] = useState<ProviderModelsResponse[]>([]);
  const [agentTeamProfiles, setAgentTeamProfiles] = useState<AgentTeamProfileResponse[]>([]);
  const [workflowProfiles, setWorkflowProfiles] = useState<WorkflowProfileResponse[]>([]);
  const [form, setForm] = useState<SessionCreate>({
    incident_id: incidentId,
    workflow_profile_id: undefined,
    agent_team_profile_id: undefined,
    tier: 2,
    model_provider: undefined,
    model_id: undefined,
    initial_briefing: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) {
      listProviders()
        .then((res) => setProviders(res.items))
        .catch(() => {});
      listAgentTeamProfiles()
        .then((res) => setAgentTeamProfiles(res.items.filter((item) => item.is_active)))
        .catch(() => {});
      listWorkflowProfiles()
        .then((res) => setWorkflowProfiles(res.items.filter((item) => item.is_active)))
        .catch(() => {});
    }
  }, [open]);

  const selectedProvider = providers.find((p) => p.provider === form.model_provider);
  // Tier 1 / Tier 2 sessions can only be started once the incident is
  // acknowledged (the backend enforces this; we surface it up-front). A Tier 0
  // session does not require an ack.
  const ackRequired = form.tier === 1 || form.tier === 2;
  const blockedOnAck = ackRequired && !isAcknowledged;

  async function handleStart() {
    if (
      form.force
      && !window.confirm(
        "Force start can exceed the selected model's concurrency limit. "
          + "The override will be audited and will count toward occupancy. Continue?",
      )
    ) {
      return;
    }
    setError("");
    setLoading(true);
    try {
      const payload: SessionCreate = {
        ...form,
        initial_briefing: form.initial_briefing?.trim() || undefined,
      };
      const session = await createSession(payload);
      onStarted(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start session");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Start Session">
      <div className="space-y-4">
        <div>
          <Label htmlFor="ss-agent-team">Agent Team (optional)</Label>
          <Select
            id="ss-agent-team"
            value={form.agent_team_profile_id ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, agent_team_profile_id: e.target.value || undefined }))
            }
          >
            <option value="">Default</option>
            {agentTeamProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}{profile.is_default ? " (default)" : ""}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="ss-workflow">Session Profile (optional)</Label>
          <Select
            id="ss-workflow"
            value={form.workflow_profile_id ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, workflow_profile_id: e.target.value || undefined }))
            }
          >
            <option value="">Default</option>
            {workflowProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}{profile.is_default ? " (default)" : ""}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="ss-tier">AI Autonomy Tier</Label>
          <Select
            id="ss-tier"
            value={form.tier}
            onChange={(e) => setForm((f) => ({ ...f, tier: Number(e.target.value) }))}
          >
            <option value={0}>Tier 0 — Autonomous</option>
            <option value={1}>Tier 1 — Approval Required</option>
            <option value={2}>Tier 2 — Advisory Only</option>
          </Select>
          <p className="mt-1.5 text-xs text-fg-muted">
            How much autonomy the AI has this session — separate from incident
            priority and your role. Tier 2 (Advisory) is the default.
          </p>
          {form.tier === 0 && (
            <p className="mt-1.5 text-xs font-medium text-status-critical">
              Tier 0 allows autonomous remediation, including destructive
              operations when allowed by MCP Skill policy. Use only when you
              trust the connected MCP server, skill policy, and environment.
            </p>
          )}
          {blockedOnAck && (
            <p className="mt-1.5 text-xs font-medium text-status-medium">
              Acknowledge the incident first — Tier {form.tier} sessions can only
              be started after an operator acknowledges it.
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="ss-provider">Model Provider (optional)</Label>
          <Select
            id="ss-provider"
            value={form.model_provider ?? ""}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                model_provider: e.target.value || undefined,
                model_id: undefined,
              }))
            }
          >
            <option value="">Default</option>
            {providers.map((p) => (
              <option key={p.provider} value={p.provider} disabled={!p.available}>
                {p.label}{!p.available ? " (unavailable)" : ""}
              </option>
            ))}
          </Select>
        </div>

        {selectedProvider && selectedProvider.models.length > 0 && (
          <div>
            <Label htmlFor="ss-model">Model</Label>
            <Select
              id="ss-model"
              value={form.model_id ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, model_id: e.target.value || undefined }))}
            >
              <option value="">Default ({selectedProvider.default_model_id})</option>
              {selectedProvider.models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </Select>
          </div>
        )}

        <div>
          <Label htmlFor="ss-briefing">Initial briefing (optional)</Label>
          <Textarea
            id="ss-briefing"
            rows={4}
            placeholder="What do you already know about this incident? Any context, hypotheses, or logs you want the co-pilot to start with…"
            value={form.initial_briefing ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, initial_briefing: e.target.value }))
            }
          />
          <p className="mt-1 text-xs text-fg-muted">
            Seeded as the first user message in the co-pilot chat before the workflow begins.
          </p>
        </div>

        <label className="flex items-start gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={Boolean(form.force)}
            onChange={(e) =>
              setForm((current) => ({
                ...current,
                force: e.target.checked,
              }))
            }
          />
          <span>
            <span className="block font-medium text-fg-primary">
              Force start if the selected model is full
            </span>
            <span className="block text-xs text-fg-muted">
              Soft override: the session counts toward occupancy and the action
              is audited.
            </span>
          </span>
        </label>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleStart} loading={loading} disabled={blockedOnAck}>
            <Play size={13} /> Start
          </Button>
        </div>
      </div>
    </Modal>
  );
}
