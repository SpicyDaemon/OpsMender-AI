"use client";

/**
 * Sprint A Step 1 - Incident Command Strip.
 *
 * Sticky action bar at the top of the incident detail page. Surfaces
 * the lifecycle actions the operator needs at-a-glance: Acknowledge,
 * Take, Start session, Resolve, Create postmortem.
 *
 * Action visibility is driven by incident status + paging assignment
 * state so the operator never sees an action that would no-op:
 *
 * | status       | shown                                                |
 * |--------------|------------------------------------------------------|
 * | open         | Acknowledge, Take/Release, Start session, Resolve    |
 * | in_progress  | Take/Release, Start session, Resolve                 |
 * | resolved     | Create postmortem                                    |
 *
 * Approve / Reject + Escalate land in Sprint A step 2 (right-rail
 * context) and Sprint B (governed AI) - they need state the detail
 * page doesn't currently surface (pending approvals + chain state).
 */

import { useState } from "react";
import {
  Check,
  CheckCircle2,
  ChevronRight,
  Hand,
  HandMetal,
  Loader2,
  Play,
  ScrollText,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/context/auth";
import {
  ackIncident,
  assignIncident,
  bulkIncidentAction,
  deleteIncident,
  releaseIncident,
  takeIncident,
} from "@/lib/api";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";
import type {
  IncidentAssignmentResponse,
  IncidentResponse,
  PendingTakeover,
} from "@/lib/types";

type Status = IncidentResponse["status"];

interface Props {
  incident: IncidentResponse;
  assignment: IncidentAssignmentResponse | null;
  /** Opens the existing start-session modal. */
  onStartSession: () => void;
  /** Re-fetch parent state after an action mutates the incident. */
  onChanged: () => Promise<void> | void;
  /** Resolved owner display label for "assigned to someone else" states. */
  ownerLabel?: string | null;
  /** A live request to take the incident over from its owner. */
  pendingTakeover?: PendingTakeover | null;
  /** Optional: collapses extra status pills on narrow viewports. */
  className?: string;
}

export function IncidentCommandStrip({
  incident,
  assignment,
  onStartSession,
  onChanged,
  ownerLabel,
  pendingTakeover = null,
  className,
}: Props) {
  const toast = useToast();
  const navigateDashboard = useDashboardNavigation();
  const { user } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);

  const status = incident.status as Status;
  const isOpen = status === "open";
  const isResolved = status === "resolved";

  const isAssignedToMe =
    assignment !== null &&
    assignment.released_at === null &&
    user !== null &&
    assignment.assigned_to === user.id;
  const isAssignedToSomeoneElse =
    assignment !== null &&
    assignment.released_at === null &&
    !isAssignedToMe;
  const takeoverRequestedByMe =
    pendingTakeover !== null && user !== null && pendingTakeover.user_id === user.id;
  const owner = ownerLabel || "The owner";

  // -- Action handlers -----------------------------------------------------

  async function run(name: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(name);
    try {
      await fn();
      toast.success(ok);
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleAck() {
    setBusy("ack");
    try {
      const res = await ackIncident(incident.id, "web_ui");
      const msg = res?.auto_start_message || "Acknowledged";
      if (res?.auto_start_status === "failed") {
        toast.warning(msg);
      } else {
        toast.success(msg);
      }
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }
  // Taking an incident someone is actively working needs their OK (S-112).
  async function handleTake() {
    setBusy("take");
    try {
      if (isAssignedToSomeoneElse) {
        await takeIncident(incident.id);
        toast.success(`Asked ${owner} to hand it over. They have five minutes.`);
      } else {
        // The server rejects stale assignment data if another owner got here first.
        await assignIncident(incident.id);
        toast.success("You now own this incident");
      }
      await onChanged();
    } catch (err) {
      if ((err as { status?: number }).status === 409 && !isAssignedToSomeoneElse) {
        try {
          await takeIncident(incident.id);
          toast.success("Asked the new owner to hand it over. They have five minutes.");
          await onChanged();
        } catch (inner) {
          toast.error(inner instanceof Error ? inner.message : String(inner));
        }
      } else {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(null);
    }
  }
  const handleHandOver = () =>
    run(
      "handover",
      () => takeIncident(incident.id, { confirm: true }),
      `Handed over to ${pendingTakeover?.username ?? "them"}`,
    );
  const handleForceTake = () => {
    if (
      !confirm(
        `Take over from ${owner} now, without waiting for them to hand it over?`,
      )
    ) {
      return;
    }
    void run(
      "force",
      () => takeIncident(incident.id, { force: true }),
      "You now own this incident",
    );
  };
  const handleRelease = () =>
    run("release", () => releaseIncident(incident.id), "Released ownership");
  const handleResolve = () =>
    run(
      "resolve",
      () => bulkIncidentAction("resolve", [incident.id]),
      "Incident resolved",
  );
  const handlePostmortem = () => {
    navigateDashboard(`/dashboard/incidents/postmortem?id=${incident.id}`);
  };
  const handleDelete = async () => {
    if (
      !window.confirm(
        `Permanently delete incident "${incident.title}"? This also removes its sessions and operational history. This action cannot be undone.`,
      )
    ) {
      return;
    }
    setBusy("delete");
    try {
      await deleteIncident(incident.id);
      toast.success("Incident permanently deleted.");
      navigateDashboard("/dashboard/incidents");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  };

  // -- Status pill (replaces the existing badge row's status pill) -------

  const statusLabel: Record<Status, string> = {
    open: "Open",
    in_progress: "In progress",
    resolved: "Resolved",
    merged: "Merged",
  };

  return (
    <div
      className={[
        "sticky top-0 z-20 -mx-4 mb-4 border-b border-border-subtle bg-bg-base/85 px-4 py-3 backdrop-blur-md supports-[backdrop-filter]:bg-bg-base/75 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8",
        className ?? "",
      ].join(" ")}
      data-testid="incident-command-strip"
      aria-busy={busy !== null}
      aria-live="polite"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:gap-4">
        {/* Left: status + severity + truncated title */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Badge
            variant={status as Parameters<typeof Badge>[0]["variant"]}
          >
            {statusLabel[status] ?? status}
          </Badge>
          {incident.severity && (
            <Badge variant={incident.severity}>{incident.severity}</Badge>
          )}
          {isAssignedToMe && (
            <Badge variant="default" className="hidden sm:inline-flex">
              You own this
            </Badge>
          )}
          {isAssignedToSomeoneElse && assignment && (
            <Badge variant="default" className="hidden md:inline-flex">
              Owner: {ownerLabel || "Assigned"}
            </Badge>
          )}
          <h2
            className="truncate text-sm font-semibold text-fg-primary sm:text-base"
            title={incident.title}
          >
            {incident.title}
          </h2>
        </div>

        {/* Right: actions */}
        <div className="flex flex-wrap items-center gap-2">
          {isOpen && (
            <Button
              size="sm"
              variant="secondary"
              disabled={!!busy}
              onClick={handleAck}
              data-testid="action-acknowledge"
            >
              {busy === "ack" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Check size={14} />
              )}
              Acknowledge
            </Button>
          )}

          {!isResolved && !isAssignedToMe && (
            <Button
              size="sm"
              variant="secondary"
              disabled={!!busy || takeoverRequestedByMe}
              onClick={handleTake}
              data-testid="action-take"
              title={
                takeoverRequestedByMe
                  ? `Waiting for ${owner} to hand it over`
                  : isAssignedToSomeoneElse
                    ? `${owner} owns it: if they're working on it, they'll be asked to hand it over`
                    : "Assign this incident to yourself"
              }
            >
              {busy === "take" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Hand size={14} />
              )}
              {takeoverRequestedByMe
                ? "Takeover requested"
                : isAssignedToSomeoneElse
                  ? "Take over"
                  : "Take"}
            </Button>
          )}

          {!isResolved && isAssignedToSomeoneElse && user?.role === "admin" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={handleForceTake}
              data-testid="action-force-take"
              title={`Take over from ${owner} without waiting for them`}
            >
              {busy === "force" ? <Loader2 size={14} className="animate-spin" /> : null}
              Force take
            </Button>
          )}

          {!isResolved && isAssignedToMe && pendingTakeover && (
            <Button
              size="sm"
              disabled={!!busy}
              onClick={handleHandOver}
              data-testid="action-hand-over"
              title={`${pendingTakeover.username} asked to take over this incident`}
            >
              {busy === "handover" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Hand size={14} />
              )}
              Hand over to {pendingTakeover.username}
            </Button>
          )}

          {isAssignedToMe && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={handleRelease}
              data-testid="action-release"
              title="Hand this back to the on-call roster"
            >
              {busy === "release" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <HandMetal size={14} />
              )}
              Release
            </Button>
          )}

          {!isResolved && (
            <Button
              size="sm"
              disabled={!!busy}
              onClick={onStartSession}
              data-testid="action-start-session"
            >
              <Play size={14} />
              Start session
            </Button>
          )}

          {!isResolved && (
            <Button
              size="sm"
              variant="secondary"
              disabled={!!busy}
              onClick={handleResolve}
              data-testid="action-resolve"
            >
              {busy === "resolve" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <CheckCircle2 size={14} />
              )}
              Resolve
            </Button>
          )}


          {isResolved && (
            <Button
              size="sm"
              variant="secondary"
              onClick={handlePostmortem}
              data-testid="action-postmortem"
              title="Capture what happened, what we learned, and which memories to add"
            >
              <ScrollText size={14} />
              Create postmortem
              <ChevronRight size={14} />
            </Button>
          )}

          {user?.role === "admin" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={() => void handleDelete()}
              data-testid="action-delete"
              title={`Delete incident ${incident.title}`}
              aria-label={`Delete incident ${incident.title}`}
            >
              {busy === "delete" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Trash2 size={14} />
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
