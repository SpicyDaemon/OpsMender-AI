"use client";

/**
 * Sprint 57 Step 2 — Incident Context Rail.
 *
 * Right-side panel surfacing the operational context an on-call
 * operator needs at a glance:
 *
 *   Status / Severity         (always)
 *   Service                   (if incident.service_id is set)
 *   Team                      (resolved via service.team_id)
 *   Current owner             (from pagingPanel.assignment)
 *   Escalation step           (from chain state)
 *   Pending approvals         (count of pending approval requests
 *                              across the incident's sessions)
 *   AI tier                   (tier of the most recent active session,
 *                              else "—")
 *
 * The component fetches its own supplemental data (services, teams,
 * users, chain state, approvals) so the parent detail page doesn't
 * grow more orchestration.
 *
 * On-call resolution is *deferred* to Sprint 57 Step 3 because the
 * paging panel doesn't currently carry the team's roster id; resolving
 * "who's on call right now" would mean an extra roster lookup per
 * mount. The escalation-step row covers most of that need today.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AlertOctagon,
  Bot,
  CalendarClock,
  GitBranch,
  Server,
  ShieldAlert,
  User as UserIcon,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { useAuth } from "@/context/auth";
import {
  getIncidentChain,
  listApprovals,
  listServices,
  listTeams,
  listUsers,
} from "@/lib/api";
import type {
  IncidentChainPanelResponse,
  IncidentPagingPanelResponse,
  IncidentResponse,
  ServiceResponse,
  SessionResponse,
  TeamResponse,
  UserResponse,
} from "@/lib/types";

interface Props {
  incident: IncidentResponse;
  pagingPanel: IncidentPagingPanelResponse | null;
  sessions: SessionResponse[];
}

export function IncidentContextRail({
  incident,
  pagingPanel,
  sessions,
}: Props) {
  const { user } = useAuth();
  const canReadUserDirectory =
    user?.role === "admin" || user?.role === "operator";
  const [services, setServices] = useState<ServiceResponse[]>([]);
  const [teams, setTeams] = useState<TeamResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [chain, setChain] = useState<IncidentChainPanelResponse | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);

  // -- Supplemental fetches ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listServices().catch(() => ({ items: [], total: 0 })),
      listTeams().catch(() => ({ items: [], total: 0 })),
      canReadUserDirectory
        ? listUsers().catch(() => ({ items: [], total: 0 }))
        : Promise.resolve({ items: [], total: 0 }),
      getIncidentChain(incident.id).catch(
        () => null as IncidentChainPanelResponse | null,
      ),
    ]).then(([svc, tms, usr, ch]) => {
      if (cancelled) return;
      setServices(svc.items);
      setTeams(tms.items);
      setUsers(usr.items);
      setChain(ch);
    });
    return () => {
      cancelled = true;
    };
  }, [canReadUserDirectory, incident.id]);

  // -- Approval count across all incident sessions -------------------------
  useEffect(() => {
    let cancelled = false;
    if (sessions.length === 0) {
      return;
    }
    Promise.all(
      sessions.map((s) =>
        listApprovals({ session_id: s.id, status: "pending" }).catch(() => ({
          items: [],
          total: 0,
        })),
      ),
    ).then((results) => {
      if (cancelled) return;
      const total = results.reduce((sum, r) => sum + r.items.length, 0);
      setPendingApprovals(total);
    });
    return () => {
      cancelled = true;
    };
  }, [sessions]);

  // -- Derived lookups -----------------------------------------------------
  const service = useMemo(
    () =>
      incident.service_id
        ? services.find((s) => s.id === incident.service_id) ?? null
        : null,
    [incident.service_id, services],
  );
  const team = useMemo(
    () => (service ? teams.find((t) => t.id === service.team_id) ?? null : null),
    [service, teams],
  );
  const owner = useMemo(() => {
    const a = pagingPanel?.assignment;
    if (!a || a.released_at !== null) return null;
    return users.find((u) => u.id === a.assigned_to) ?? null;
  }, [pagingPanel, users]);

  const activeSession = useMemo(
    () =>
      sessions.find(
        (s) => s.status === "active" || s.status === "awaiting_approval",
      ) ?? null,
    [sessions],
  );
  const latestTier = activeSession?.tier ?? sessions[0]?.tier ?? null;
  const visiblePendingApprovals =
    sessions.length === 0 ? 0 : pendingApprovals;

  const chainState = chain?.state ?? null;
  const escalationStepLabel = chainState
    ? `Step ${chainState.current_step_index + 1} · ${chainState.status}`
    : null;

  // -- Render --------------------------------------------------------------
  return (
    <aside
      className="space-y-4"
      aria-label="Incident context"
      data-testid="incident-context-rail"
    >
      <div className="rounded-xl border border-border-subtle bg-bg-panel shadow-sm">
        <div className="border-b border-border-subtle px-5 py-4">
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Context
          </p>
          <h2 className="mt-1 text-lg font-semibold text-fg-primary">
            What you&apos;re working with
          </h2>
        </div>
        <dl className="space-y-3 px-5 py-5 text-sm">
          <Row icon={ShieldAlert} label="Status">
            <Badge
              variant={incident.status as Parameters<typeof Badge>[0]["variant"]}
            >
              {incident.status.replace("_", " ")}
            </Badge>
          </Row>
          <Row icon={AlertOctagon} label="Severity">
            {incident.severity ? (
              <Badge variant={incident.severity}>{incident.severity}</Badge>
            ) : (
              <Muted>Not set</Muted>
            )}
          </Row>

          {service ? (
            <Row icon={Server} label="Service">
              <Link
                href="/dashboard/paging/services"
                className="font-medium text-fg-primary hover:text-accent-text"
              >
                {service.name}
              </Link>
            </Row>
          ) : (
            <Row icon={Server} label="Service">
              <Muted>Unbound</Muted>
            </Row>
          )}

          {team ? (
            <Row icon={Users} label="Team">
              <Link
                href="/dashboard/paging/teams"
                className="text-fg-primary hover:text-accent-text"
              >
                {team.name}
              </Link>
            </Row>
          ) : (
            <Row icon={Users} label="Team">
              <Muted>—</Muted>
            </Row>
          )}

          <Row icon={UserIcon} label="Current owner">
            {owner ? (
              <span className="text-fg-primary">{owner.username}</span>
            ) : (
              <Muted>Unassigned</Muted>
            )}
          </Row>

          <Row icon={GitBranch} label="Escalation">
            {escalationStepLabel ? (
              <span className="text-fg-primary">{escalationStepLabel}</span>
            ) : (
              <Muted>No active chain</Muted>
            )}
          </Row>

          <Row icon={CalendarClock} label="Pending approvals">
            {visiblePendingApprovals === null ? (
              <Muted>…</Muted>
            ) : visiblePendingApprovals === 0 ? (
              <Muted>None</Muted>
            ) : (
              <Badge variant="high">{visiblePendingApprovals}</Badge>
            )}
          </Row>

          <Row icon={Bot} label="AI tier">
            {latestTier !== null ? (
              <Badge variant="default">Tier {latestTier}</Badge>
            ) : (
              <Muted>No session yet</Muted>
            )}
          </Row>
        </dl>
      </div>
    </aside>
  );
}

function Row({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof ShieldAlert;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="flex items-center gap-2 text-fg-muted">
        <Icon size={14} className="shrink-0" />
        {label}
      </dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-fg-muted">{children}</span>;
}
