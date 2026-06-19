"use client";

/**
 * Sprint A Step 1 — Incident Command Strip.
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
 * context) and Sprint B (governed AI) — they need state the detail
 * page doesn't currently surface (pending approvals + chain state).
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
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
} from "@/lib/api";
import type {
  IncidentAssignmentResponse,
  IncidentResponse,
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
  /** Optional: collapses extra status pills on narrow viewports. */
  className?: string;
}

export function IncidentCommandStrip({
  incident,
  assignment,
  onStartSession,
  onChanged,
  ownerLabel,
  className,
}: Props) {
  const toast = useToast();
  const router = useRouter();
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
  const handleTake = () =>
    run(
      "take",
      () =>
        // assignIncident with no user id self-assigns to the caller.
        assignIncident(incident.id),
      "You now own this incident",
    );
  const handleRelease = () =>
    run("release", () => releaseIncident(incident.id), "Released ownership");
  const handleResolve = () =>
    run(
      "resolve",
      () => bulkIncidentAction("resolve", [incident.id]),
      "Incident resolved",
    );
  const handlePostmortem = () => {
    router.push(`/dashboard/incidents/postmortem?id=${incident.id}`);
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
      router.push("/dashboard/incidents");
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
              disabled={!!busy}
              onClick={handleTake}
              data-testid="action-take"
              title={
                isAssignedToSomeoneElse
                  ? "Take over from the current owner"
                  : "Assign this incident to yourself"
              }
            >
              {busy === "take" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Hand size={14} />
              )}
              {isAssignedToSomeoneElse ? "Take over" : "Take"}
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
