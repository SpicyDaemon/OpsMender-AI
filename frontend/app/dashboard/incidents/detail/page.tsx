"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CalendarClock,
  ChevronRight,
  CircleDot,
  Play,
  Radar,
  ShieldAlert,
  TimerReset,
} from "lucide-react";
import {
  createSession,
  getIncident,
  listAgentTeamProfiles,
  listIncidentSessions,
  listProviders,
  listWorkflowProfiles,
} from "@/lib/api";
import type {
  AgentTeamProfileResponse,
  IncidentResponse,
  ProviderModelsResponse,
  SessionCreate,
  SessionResponse,
  WorkflowProfileResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { DetailSkeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtRelative(iso: string) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return fmtDate(iso);
}

function sourceMeta(incident: IncidentResponse) {
  if (!incident.external_source) {
    return {
      label: "Manual",
      detail: "Operator-created incident",
      tone: "bg-status-info-bg text-status-info border-status-info-border",
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
    detail: incident.external_id ?? "Webhook or detector-driven incident",
    tone: "bg-status-medium-bg text-status-medium border-status-medium-border",
  };
}

function sessionStatusSummary(sessions: SessionResponse[]) {
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

function IncidentDetailContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const router = useRouter();
  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [sessionsError, setSessionsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showSession, setShowSession] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true);
      setSessionsError("");
      try {
        const incidentRes = await getIncident(id);
        if (cancelled) return;
        setIncident(incidentRes);
      } catch (err) {
        if (!cancelled) {
          setIncident(null);
          setSessions([]);
          toast.error(err instanceof Error ? err.message : "Failed to load incident");
          setLoading(false);
        }
        return;
      }

      try {
        const sessionsRes = await listIncidentSessions(id);
        if (cancelled) return;
        setSessions(sessionsRes.items);
      } catch (err) {
        if (!cancelled) {
          setSessions([]);
          const message =
            err instanceof Error ? err.message : "Failed to load session history";
          setSessionsError(message);
          toast.warning(`Session history is temporarily unavailable: ${message}`);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [id, toast]);

  const source = useMemo(() => (incident ? sourceMeta(incident) : null), [incident]);
  const sessionSummary = useMemo(() => sessionStatusSummary(sessions), [sessions]);

  if (loading) return <DetailSkeleton />;
  if (!id) return <p className="text-status-critical">Missing incident id.</p>;
  if (!incident) return <p className="text-status-critical">Incident not found.</p>;

  return (
    <div className="mx-auto max-w-6xl">
      {/* Back */}
      <Link
        href="/dashboard/incidents"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg-primary"
      >
        <ArrowLeft size={14} /> Incidents
      </Link>

      <div className="mb-6 overflow-hidden rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="border-b border-border-subtle bg-[linear-gradient(135deg,rgba(59,130,246,0.12),transparent_45%),linear-gradient(225deg,rgba(234,179,8,0.10),transparent_50%)] px-6 py-6">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${source?.tone}`}>
                  <Radar size={12} />
                  {source?.label}
                </span>
                <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
                  {incident.status.replace("_", " ")}
                </Badge>
                {incident.severity && (
                  <Badge variant={incident.severity}>{incident.severity}</Badge>
                )}
                <Badge variant={sessionSummary.variant}>{sessionSummary.label}</Badge>
              </div>
              <h1 className="mt-4 text-3xl font-semibold tracking-tight text-fg-primary">
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

            <div className="flex min-w-[240px] flex-col gap-3">
              <Button size="lg" className="justify-center" onClick={() => setShowSession(true)}>
                <Play size={16} />
                Start Session
              </Button>
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

        <div className="grid gap-3 px-6 py-4 md:grid-cols-3">
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
            label="Session posture"
            value={sessionSummary.label}
            hint={sessions.length > 0 ? `${sessions.length} session${sessions.length === 1 ? "" : "s"} recorded` : "Ready for first response run"}
          />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
          <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                Session Timeline
              </p>
              <h2 className="mt-1 text-lg font-semibold text-fg-primary">
                Response history
              </h2>
            </div>
            <Button size="sm" variant="secondary" onClick={() => setShowSession(true)}>
              <Play size={14} />
              New Session
            </Button>
          </div>

          <div className="p-5">
            {sessionsError ? (
              <div className="rounded-lg border border-status-high-border bg-status-high-bg px-4 py-4 text-sm text-fg-secondary">
                We couldn&apos;t load session history for this incident right now. You can still review the incident details and start a new session.
              </div>
            ) : sessions.length === 0 ? (
              <EmptyState
                icon={CircleDot}
                title="No sessions for this incident yet"
                description="Start the first session to capture triage, approvals, execution, and chat history in one place."
                action={(
                  <Button size="sm" onClick={() => setShowSession(true)}>
                    <Play size={14} />
                    Start Session
                  </Button>
                )}
              />
            ) : (
              <div className="space-y-3">
                {sessions.map((session, index) => (
                  <Link
                    key={session.id}
                    href={`/dashboard/sessions/detail?id=${session.id}`}
                    className="group flex items-start gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-4 py-4 transition-colors hover:border-border-strong hover:bg-bg-hover"
                  >
                    <div className="flex flex-col items-center">
                      <span className="flex h-8 w-8 items-center justify-center rounded-md border border-border-subtle bg-bg-panel text-[11px] font-semibold text-fg-secondary">
                        S{sessions.length - index}
                      </span>
                      {index < sessions.length - 1 && (
                        <span className="mt-2 h-10 w-px bg-border-subtle" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold text-fg-primary">
                          Session {session.id.slice(0, 8)}…
                        </p>
                        <Badge variant={session.status as Parameters<typeof Badge>[0]["variant"]}>
                          {session.status.replace("_", " ")}
                        </Badge>
                        <span className="rounded-md border border-border-subtle bg-bg-panel px-2 py-0.5 font-mono text-[11px] text-fg-secondary">
                          Tier {session.tier}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
                        <span>Started {fmtDate(session.started_at)}</span>
                        <span>
                          {session.ended_at ? `Ended ${fmtDate(session.ended_at)}` : "Still active"}
                        </span>
                        {session.model_provider && (
                          <span className="font-mono">
                            {session.model_provider}/{session.model_id ?? "default"}
                          </span>
                        )}
                      </div>
                      {session.summary && (
                        <p className="mt-3 line-clamp-2 text-sm text-fg-secondary">
                          {session.summary}
                        </p>
                      )}
                    </div>
                    <ChevronRight size={16} className="mt-1 shrink-0 text-fg-muted transition-transform group-hover:translate-x-0.5" />
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <div className="border-b border-border-subtle px-5 py-4">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                Incident Details
              </p>
              <h2 className="mt-1 text-lg font-semibold text-fg-primary">Context</h2>
            </div>
            <div className="space-y-4 p-5 text-sm">
              <DetailRow label="Status">
                <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
                  {incident.status.replace("_", " ")}
                </Badge>
              </DetailRow>
              <DetailRow label="Severity">
                {incident.severity ? (
                  <Badge variant={incident.severity}>{incident.severity}</Badge>
                ) : (
                  <span className="text-fg-muted">Not set</span>
                )}
              </DetailRow>
              <DetailRow label="Source">
                <span className="text-fg-primary">{source?.label}</span>
              </DetailRow>
              <DetailRow label="Source detail">
                <span className="break-all text-fg-primary">{source?.detail}</span>
              </DetailRow>
            </div>
          </div>

          <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
            <div className="border-b border-border-subtle px-5 py-4">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                Suggested Next Step
              </p>
            </div>
            <div className="p-5">
              <p className="text-sm leading-6 text-fg-secondary">
                Use a new session when you want a fresh reasoning chain, updated chat context,
                or a different workflow/team/tier configuration for this incident.
              </p>
              <Button className="mt-4 w-full justify-center" onClick={() => setShowSession(true)}>
                <Play size={14} />
                Start Session
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Start session modal */}
      <StartSessionModal
        open={showSession}
        onClose={() => setShowSession(false)}
        incidentId={incident.id}
        onStarted={(sid) => router.push(`/dashboard/sessions/detail?id=${sid}`)}
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
  onStarted,
}: {
  open: boolean;
  onClose: () => void;
  incidentId: string;
  onStarted: (sessionId: string) => void;
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

  async function handleStart() {
    setError("");
    setLoading(true);
    try {
      const payload: SessionCreate = {
        ...form,
        initial_briefing: form.initial_briefing?.trim() || undefined,
      };
      const session = await createSession(payload);
      onStarted(session.id);
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
          <Label htmlFor="ss-workflow">Workflow Profile (optional)</Label>
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
          <Label htmlFor="ss-tier">Tier</Label>
          <Select
            id="ss-tier"
            value={form.tier}
            onChange={(e) => setForm((f) => ({ ...f, tier: Number(e.target.value) }))}
          >
            <option value={0}>Tier 0 — Autonomous rollback-safe only (time-limited)</option>
            <option value={1}>Tier 1 — Approval gate (destructive ops need approval)</option>
            <option value={2}>Tier 2 — Safe + caution only (no destructive ops)</option>
            <option value={3}>Tier 3 — Advise-only (no execution)</option>
          </Select>
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

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleStart} loading={loading}>
            <Play size={13} /> Start
          </Button>
        </div>
      </div>
    </Modal>
  );
}
