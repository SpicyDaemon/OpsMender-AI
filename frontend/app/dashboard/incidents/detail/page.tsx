"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Play } from "lucide-react";
import {
  createSession,
  getIncident,
  listAgentTeamProfiles,
  listProviders,
  listWorkflowProfiles,
} from "@/lib/api";
import type {
  AgentTeamProfileResponse,
  IncidentResponse,
  ProviderModelsResponse,
  SessionCreate,
  WorkflowProfileResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Label, Select, Textarea, FormError } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageSpinner } from "@/components/ui/Spinner";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString();
}

export default function IncidentDetailPage() {
  return (
    <Suspense fallback={<PageSpinner />}>
      <IncidentDetailContent />
    </Suspense>
  );
}

function IncidentDetailContent() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id") ?? "";
  const router = useRouter();
  const [incident, setIncident] = useState<IncidentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showSession, setShowSession] = useState(false);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    getIncident(id)
      .then(setIncident)
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <PageSpinner />;
  if (!id) return <p className="text-status-critical">Missing incident id.</p>;
  if (!incident) return <p className="text-status-critical">Incident not found.</p>;

  return (
    <div className="max-w-3xl mx-auto">
      {/* Back */}
      <Link
        href="/dashboard/incidents"
        className="inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg-primary mb-6"
      >
        <ArrowLeft size={14} /> Incidents
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-fg-primary">{incident.title}</h1>
          <div className="flex items-center gap-2 mt-2">
            <Badge variant={incident.status as Parameters<typeof Badge>[0]["variant"]}>
              {incident.status.replace("_", " ")}
            </Badge>
            {incident.severity && (
              <Badge variant={incident.severity}>{incident.severity}</Badge>
            )}
          </div>
        </div>
        <Button onClick={() => setShowSession(true)}>
          <Play size={14} />
          Start Session
        </Button>
      </div>

      {/* Detail card */}
      <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm p-6 space-y-4">
        <div>
          <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Description</p>
          <p className="text-sm text-fg-primary whitespace-pre-wrap">{incident.description}</p>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Created</p>
            <p className="text-fg-primary">{fmtDate(incident.created_at)}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-fg-muted uppercase tracking-wide mb-1">Updated</p>
            <p className="text-fg-primary">{fmtDate(incident.updated_at)}</p>
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
