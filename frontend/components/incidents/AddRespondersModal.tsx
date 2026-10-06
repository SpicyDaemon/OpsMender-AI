"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { FormError, Label, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { MultiSelect, type MultiSelectOption } from "@/components/ui/MultiSelect";
import { addIncidentResponders, listTeamMembers } from "@/lib/api";
import type { IncidentResponderResponse, UserResponse } from "@/lib/types";

/**
 * Ask up to the limit of people to help with an incident. Each one is paged
 * through their own notification settings and gets an Inbox notice. Admins
 * can ask anyone; operators ask members of the team handling the incident
 * (anyone when it has no team).
 */
export function AddRespondersModal({
  open,
  incidentId,
  teamId,
  teamName,
  canAddAnyone,
  ownerId,
  responders,
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
  limit: number;
  users: UserResponse[];
  onClose: () => void;
  onAdded: (names: string[]) => Promise<void> | void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [teammates, setTeammates] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSelected([]);
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

  const slotsLeft = Math.max(0, limit - responders.length);
  const teamOnly = !canAddAnyone && teamId !== null;

  const options = useMemo<MultiSelectOption[]>(() => {
    const taken = new Set(responders.map((responder) => responder.user_id));
    return users
      .filter(
        (user) =>
          user.is_active &&
          !user.deleted_at &&
          (user.role === "admin" || user.role === "operator") &&
          user.id !== ownerId &&
          !taken.has(user.id) &&
          (!teamOnly || teammates.has(user.id)),
      )
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
      }));
  }, [ownerId, responders, teamName, teamOnly, teammates, users]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected.length === 0) return;
    setBusy(true);
    setError("");
    try {
      await addIncidentResponders(incidentId, {
        user_ids: selected,
        message: message.trim() || undefined,
      });
      const names = options
        .filter((option) => selected.includes(option.value))
        .map((option) => option.label);
      await onAdded(names);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

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
        </p>

        <div>
          <Label>People</Label>
          <MultiSelect
            options={options}
            selected={selected}
            onChange={setSelected}
            maxSelections={slotsLeft}
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
              You can add members of {teamName ?? "the team handling this incident"}. Ask an
              admin to add someone from another team.
            </p>
          )}
        </div>

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
            disabled={busy || selected.length === 0 || slotsLeft === 0}
            data-testid="confirm-add-responders"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {selected.length > 1 ? `Add ${selected.length} responders` : "Add responder"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
