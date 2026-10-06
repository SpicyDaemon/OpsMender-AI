"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { FormError, Label, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { MultiSelect, type MultiSelectOption } from "@/components/ui/MultiSelect";
import {
  addIncidentResponders,
  listTeamMembers,
  requestIncidentResponders,
} from "@/lib/api";
import type {
  IncidentResponderRequestResponse,
  IncidentResponderResponse,
  UserResponse,
} from "@/lib/types";

/**
 * Ask up to the limit of people to help with an incident. Each one is paged
 * through their own notification settings and gets an Inbox notice. Admins
 * can add anyone; operators add members of the team handling the incident
 * (anyone when it has no team) and ask people from other teams, who join only
 * if they accept within 30 minutes. A pending request holds a slot.
 */
export function AddRespondersModal({
  open,
  incidentId,
  teamId,
  teamName,
  canAddAnyone,
  ownerId,
  responders,
  pendingRequests = [],
  limit,
  users,
  onClose,
  onAdded,
}: {
  open: boolean;
  incidentId: string;
  teamId: string | null;
  teamName: string | null;
  /** Admins add anyone; operators add members of the handling team. */
  canAddAnyone: boolean;
  ownerId: string | null;
  responders: IncidentResponderResponse[];
  /** People asked from other teams who haven't answered yet. */
  pendingRequests?: IncidentResponderRequestResponse[];
  limit: number;
  users: UserResponse[];
  onClose: () => void;
  onAdded: (added: string[], requested: string[]) => Promise<void> | void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [requested, setRequested] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [teammates, setTeammates] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSelected([]);
    setRequested([]);
    setMessage("");
    setError("");
    if (!teamId) {
      setTeammates(new Set());
      return;
    }
    let cancelled = false;
    listTeamMembers(teamId)
      .then((res) => {
        if (!cancelled) setTeammates(new Set(res.items.map((member) => member.user_id)));
      })
      .catch(() => {
        if (!cancelled) setTeammates(new Set());
      });
    return () => {
      cancelled = true;
    };
  }, [open, teamId]);

  const slotsLeft = Math.max(0, limit - responders.length - pendingRequests.length);
  const teamOnly = !canAddAnyone && teamId !== null;

  const eligible = useMemo(() => {
    const taken = new Set([
      ...responders.map((responder) => responder.user_id),
      ...pendingRequests.map((request) => request.user_id),
    ]);
    return users.filter(
      (user) =>
        user.is_active &&
        !user.deleted_at &&
        (user.role === "admin" || user.role === "operator") &&
        user.id !== ownerId &&
        !taken.has(user.id),
    );
  }, [ownerId, pendingRequests, responders, users]);

  const options = useMemo<MultiSelectOption[]>(
    () =>
      eligible
        .filter((user) => !teamOnly || teammates.has(user.id))
        .sort((a, b) => {
          const aTeam = teammates.has(a.id) ? 0 : 1;
          const bTeam = teammates.has(b.id) ? 0 : 1;
          return aTeam - bTeam || a.username.localeCompare(b.username);
        })
        .map((user) => ({
          value: user.id,
          label: user.username,
          sublabel:
            teammates.has(user.id) && teamName
              ? `${user.role === "admin" ? "Admin" : "Operator"} on ${teamName}`
              : user.role === "admin"
                ? "Admin"
                : "Operator",
        })),
    [eligible, teamName, teamOnly, teammates],
  );

  // Operators ask people outside the handling team instead of adding them.
  const requestOptions = useMemo<MultiSelectOption[]>(
    () =>
      teamOnly
        ? eligible
            .filter((user) => !teammates.has(user.id))
            .sort((a, b) => a.username.localeCompare(b.username))
            .map((user) => ({
              value: user.id,
              label: user.username,
              sublabel: user.role === "admin" ? "Admin" : "Operator",
            }))
        : [],
    [eligible, teamOnly, teammates],
  );

  const labelsFor = (ids: string[], from: MultiSelectOption[]) =>
    from.filter((option) => ids.includes(option.value)).map((option) => option.label);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected.length === 0 && requested.length === 0) return;
    setBusy(true);
    setError("");
    const note = message.trim() || undefined;
    try {
      if (selected.length > 0) {
        await addIncidentResponders(incidentId, { user_ids: selected, message: note });
      }
      if (requested.length > 0) {
        await requestIncidentResponders(incidentId, { user_ids: requested, message: note });
      }
      await onAdded(labelsFor(selected, options), labelsFor(requested, requestOptions));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const submitLabel =
    requested.length === 0
      ? selected.length > 1
        ? `Add ${selected.length} responders`
        : "Add responder"
      : selected.length === 0
        ? requested.length > 1
          ? `Send ${requested.length} requests`
          : "Send request"
        : "Add and send requests";

  return (
    <Modal open={open} onClose={() => { if (!busy) onClose(); }} title="Add responders">
      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm text-fg-secondary">
          Ask up to {limit} people to help. Each one is paged through their own
          notification settings. {slotsLeft === limit
            ? null
            : slotsLeft === 0
              ? "All responder slots are taken; remove someone first."
              : `${slotsLeft} of ${limit} slots left.`}
          {pendingRequests.length > 0
            ? ` ${pendingRequests.length} waiting for an answer.`
            : null}
        </p>

        <div>
          <Label>People</Label>
          <MultiSelect
            options={options}
            selected={selected}
            onChange={setSelected}
            maxSelections={Math.max(0, slotsLeft - requested.length)}
            placeholder="Search people…"
            emptyLabel={
              teamOnly
                ? `Nobody else on ${teamName ?? "this team"} can respond to incidents.`
                : "Nobody else can respond to incidents yet."
            }
            ariaLabel="People to add as responders"
          />
          {teamOnly && (
            <p className="mt-1.5 text-xs text-fg-muted" data-testid="responders-team-only">
              You can add members of {teamName ?? "the team handling this incident"} directly.
              People from another team join only if they accept your request.
            </p>
          )}
        </div>

        {teamOnly && (
          <div data-testid="responder-requests-section">
            <Label>Ask someone from another team</Label>
            <MultiSelect
              options={requestOptions}
              selected={requested}
              onChange={setRequested}
              maxSelections={Math.max(0, slotsLeft - selected.length)}
              placeholder="Search people…"
              emptyLabel="Nobody from another team can respond to incidents."
              ariaLabel="People from other teams to ask"
            />
            <p className="mt-1.5 text-xs text-fg-muted">
              They get an Inbox notice and an email, and have 30 minutes to accept. A
              request holds a slot until they answer.
            </p>
          </div>
        )}

        <div>
          <Label htmlFor="responder-message">Message (optional)</Label>
          <Textarea
            id="responder-message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Can you check the replica lag on orders-db?"
            maxLength={500}
            rows={2}
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={busy || (selected.length === 0 && requested.length === 0) || slotsLeft === 0}
            data-testid="confirm-add-responders"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {submitLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
