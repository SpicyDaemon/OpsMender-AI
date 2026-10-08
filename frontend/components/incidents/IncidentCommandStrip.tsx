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
 * | open         | Acknowledge, Take/Release, Reassign, Add responders, |
 * |              | Start session, Resolve                               |
 * | in_progress  | Take/Release, Reassign, Add responders,              |
 * |              | Start session, Resolve                               |
 * | resolved     | Create postmortem                                    |
 *
 * Acknowledge, Take, Reassign, Add responders and Resolve show only when
 * the server allows them. Start session is enabled for the incident's owner
 * and admins, and shown disabled to others who could take it first.
 *
 * Approve / Reject + Escalate land in Sprint A step 2 (right-rail
 * context) and Sprint B (governed AI) - they need state the detail
 * page doesn't currently surface (pending approvals + chain state).
 */

import { useState, type FormEvent } from "react";
import {
  ArrowRightLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  Hand,
  HandMetal,
  Loader2,
  Play,
  ScrollText,
  Trash2,
  UserPlus,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Label, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { AddRespondersModal } from "@/components/incidents/AddRespondersModal";
import { ReassignIncidentModal } from "@/components/incidents/ReassignIncidentModal";
import { useAuth } from "@/context/auth";
import {
  ackIncident,
  assignIncident,
  bulkIncidentAction,
  deleteIncident,
  listIncidentsMergedInto,
  releaseIncident,
  takeIncident,
} from "@/lib/api";
import { mergedDeleteNote } from "@/lib/mergedDelete";
import { useDashboardNavigation } from "@/lib/use-dashboard-navigation";
import type {
  IncidentAssignmentResponse,
  IncidentResponderRequestResponse,
  IncidentResponderResponse,
  IncidentResponse,
  PendingTakeover,
  UserResponse,
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
  /** Admins, operators on the incident's team, and people its Escalation
   *  Chain paged in the current run can take or acknowledge it. */
  canTake?: boolean;
  /** The server-authorized force path for an admin or service teammate. */
  canForceTake?: boolean;
  /** Admins and operators on the incident's team can hand it to another team. */
  canReassign?: boolean;
  /** Admins and operators on the incident's team can resolve it. */
  canResolve?: boolean;
  /** The owner and admins start the incident's AI session; others watch. */
  canControlSession?: boolean;
  /** Admins, the owner, and operators on the incident's team. */
  canManageResponders?: boolean;
  /** People asked to help besides the owner. */
  responders?: IncidentResponderResponse[];
  /** People from other teams asked to join; each holds a slot. */
  responderRequests?: IncidentResponderRequestResponse[];
  responderLimit?: number;
  /** Workspace people, for choosing responders. */
  users?: UserResponse[];
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
  canTake = false,
  canForceTake = false,
  canReassign = false,
  canResolve = false,
  canControlSession = false,
  canManageResponders = false,
  responders = [],
  responderRequests = [],
  responderLimit = 3,
  users = [],
  className,
}: Props) {
  const toast = useToast();
  const navigateDashboard = useDashboardNavigation();
  const { user } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [forceOpen, setForceOpen] = useState(false);
  const [forceReason, setForceReason] = useState("");
  const [reassignOpen, setReassignOpen] = useState(false);
  const [respondersOpen, setRespondersOpen] = useState(false);

  const status = incident.status as Status;
  const isOpen = status === "open";
  const isResolved = status === "resolved";
  const isClosed = isResolved || status === "merged";
  const respondersFull = responders.length + responderRequests.length >= responderLimit;

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
        toast.success(`Asked ${owner} to hand it over. The request expires in five minutes.`);
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
          toast.success("Asked the new owner to hand it over. The request expires in five minutes.");
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
  async function handleForceTake(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = forceReason.trim();
    if (!reason) return;
    setBusy("force");
    try {
      await takeIncident(incident.id, { force: true, reason });
      toast.success("You now own this incident. The previous owner was notified.");
      setForceOpen(false);
      setForceReason("");
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }
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
    let note = "";
    try {
      const merged = await listIncidentsMergedInto([incident.id]);
      note = mergedDeleteNote(merged.items.map((item) => item.title));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      return;
    }
    if (
      !window.confirm(
        `Permanently delete incident "${incident.title}"? This removes it with its timeline and AI sessions. Activity keeps a record of the deletion and of the earlier session entries. This action cannot be undone.${note}`,
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
    <>
    <div
      className={[
        "sticky top-0 z-20 -mx-4 mb-4 border-b border-border-subtle bg-bg-base/85 px-4 py-3 backdrop-blur-md supports-[backdrop-filter]:bg-bg-base/75 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8",
        className ?? "",
      ].join(" ")}
      data-testid="incident-command-strip"
      aria-busy={busy !== null}
      aria-live="polite"
    >
      {/* The actions move to their own row when they would squeeze the title. */}
      <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center lg:gap-x-4">
        {/* Left: status + severity + truncated title */}
        <div className="flex min-w-0 flex-1 items-center gap-2 lg:min-w-[22rem]">
          <Badge
            variant={status as Parameters<typeof Badge>[0]["variant"]}
          >
            {statusLabel[status] ?? status}
          </Badge>
          {incident.severity && (
            <Badge variant={incident.severity}>{incident.severity}</Badge>
          )}
          {isAssignedToMe && (
            <Badge variant="default" className="hidden whitespace-nowrap sm:inline-flex">
              You own this
            </Badge>
          )}
          {isAssignedToSomeoneElse && assignment && (
            <Badge variant="default" className="hidden whitespace-nowrap md:inline-flex">
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
        <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
          {/* Someone else's incident is asked for through Take over. */}
          {isOpen && canTake && !isAssignedToSomeoneElse && (
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

          {!isResolved && !isAssignedToMe && canTake && (
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

          {!isResolved && isAssignedToSomeoneElse && canForceTake && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!!busy}
              onClick={() => setForceOpen(true)}
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

          {!isClosed && canReassign && (
            <Button
              size="sm"
              variant="secondary"
              disabled={!!busy}
              onClick={() => setReassignOpen(true)}
              data-testid="action-reassign"
              title="Hand this incident to the team it belongs to"
            >
              <ArrowRightLeft size={14} />
              Reassign
            </Button>
          )}

          {!isClosed && canManageResponders && (
            <Button
              size="sm"
              variant="secondary"
              disabled={!!busy || respondersFull}
              onClick={() => setRespondersOpen(true)}
              data-testid="action-add-responders"
              title={
                respondersFull
                  ? `All ${responderLimit} responder slots are taken`
                  : `Ask up to ${responderLimit} people to help`
              }
            >
              <UserPlus size={14} />
              Add responders
            </Button>
          )}

          {!isResolved && (canControlSession || canTake) && (
            <Button
              size="sm"
              disabled={!!busy || !canControlSession}
              onClick={onStartSession}
              data-testid="action-start-session"
              title={
                canControlSession
                  ? undefined
                  : "Take the incident first. Only its owner or an admin can start its AI session."
              }
            >
              <Play size={14} />
              Start session
            </Button>
          )}

          {!isResolved && canResolve && (
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
    <Modal
      open={forceOpen}
      onClose={() => { if (!busy) setForceOpen(false); }}
      title={`Force take from ${owner}`}
    >
      <form onSubmit={handleForceTake} className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Ownership changes immediately. {owner} will be notified, and your
          reason will appear on the incident timeline.
        </p>
        <div>
          <Label htmlFor="force-take-reason" required>Reason</Label>
          <Textarea
            id="force-take-reason"
            value={forceReason}
            onChange={(event) => setForceReason(event.target.value)}
            placeholder="Emergency database errors are causing rising 5xx responses"
            maxLength={500}
            required
            autoFocus
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={!!busy} onClick={() => setForceOpen(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" disabled={!!busy || !forceReason.trim()} data-testid="confirm-force-take">
            {busy === "force" ? <Loader2 size={14} className="animate-spin" /> : null}
            Force take
          </Button>
        </div>
      </form>
    </Modal>
    <ReassignIncidentModal
      open={reassignOpen}
      incidentId={incident.id}
      onClose={() => setReassignOpen(false)}
      onReassigned={async (teamName, paged) => {
        setReassignOpen(false);
        toast.success(
          paged
            ? `Reassigned to ${teamName}. Paging their Escalation Chain.`
            : `Reassigned to ${teamName}.`,
        );
        await onChanged();
      }}
    />
    <AddRespondersModal
      open={respondersOpen}
      incidentId={incident.id}
      teamId={incident.team_id ?? null}
      teamName={incident.team_name ?? null}
      canAddAnyone={user?.role === "admin"}
      ownerId={
        assignment && assignment.released_at === null ? assignment.assigned_to : null
      }
      responders={responders}
      pendingRequests={responderRequests}
      limit={responderLimit}
      users={users}
      onClose={() => setRespondersOpen(false)}
      onAdded={async (added, requested) => {
        setRespondersOpen(false);
        const parts = [];
        if (added.length > 0) parts.push(`Asked ${added.join(", ")} to help`);
        if (requested.length > 0) {
          parts.push(
            `${added.length > 0 ? "asked" : "Asked"} ${requested.join(", ")} to join; they have 30 minutes to accept`,
          );
        }
        toast.success(parts.join(" and "));
        await onChanged();
      }}
    />
    </>
  );
}
