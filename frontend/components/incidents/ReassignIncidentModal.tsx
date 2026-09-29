"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { FormError, Label, Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { getReassignOptions, reassignIncident } from "@/lib/api";
import type { IncidentReassignOptionsResponse } from "@/lib/types";

/**
 * Move an incident to the team it belongs to. The receiving team's Escalation
 * Chain pages from the first level; nobody picks a chain or a person.
 */
export function ReassignIncidentModal({
  open,
  incidentId,
  onClose,
  onReassigned,
}: {
  open: boolean;
  incidentId: string;
  onClose: () => void;
  onReassigned: (teamName: string, paged: boolean) => Promise<void> | void;
}) {
  const [options, setOptions] = useState<IncidentReassignOptionsResponse | null>(null);
  const [teamId, setTeamId] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setOptions(null);
    setTeamId("");
    setNote("");
    setError("");
    getReassignOptions(incidentId)
      .then((res) => {
        if (!cancelled) setOptions(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [incidentId, open]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const choice = options?.options.find((option) => option.team_id === teamId);
    if (!choice) return;
    setBusy(true);
    setError("");
    try {
      await reassignIncident(incidentId, { team_id: teamId, note: note.trim() || undefined });
      await onReassigned(choice.team_name, choice.chain_id !== null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const current = options?.current_team_name;

  return (
    <Modal open={open} onClose={() => { if (!busy) onClose(); }} title="Reassign to another team">
      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm text-fg-secondary">
          {current ? <><strong>{current}</strong> handles this incident now. </> : null}
          Pick the team it belongs to. Their Escalation Chain pages from the first
          level, and the current owner is released.
        </p>

        {!options && !error && (
          <p className="flex items-center gap-2 text-sm text-fg-muted">
            <Loader2 size={14} className="animate-spin" /> Loading teams…
          </p>
        )}

        {options && options.options.length === 0 && (
          <p className="text-sm text-fg-muted">
            There are no other teams yet. Add one in Paging, under Teams.
          </p>
        )}

        {options && options.options.length > 0 && (
          <div className="max-h-72 space-y-2 overflow-y-auto" role="radiogroup" aria-label="Team">
            {options.options.map((option) => (
              <label
                key={option.team_id}
                className="flex cursor-pointer items-start gap-3 rounded-lg border border-border-subtle p-3 hover:border-border-strong"
              >
                <input
                  type="radio"
                  name="reassign-team"
                  className="mt-1"
                  checked={teamId === option.team_id}
                  onChange={() => setTeamId(option.team_id)}
                />
                <div className="min-w-0">
                  <span className="font-medium text-fg-primary">{option.team_name}</span>
                  {option.chain_name ? (
                    <p className="mt-0.5 text-[11px] text-fg-muted">
                      Pages <span className="font-medium text-fg-secondary">{option.chain_name}</span>{" "}
                      from the first level
                    </p>
                  ) : option.note ? (
                    <p
                      className={`mt-0.5 text-[11px] ${
                        options.pages ? "text-status-medium" : "text-fg-muted"
                      }`}
                    >
                      {option.note}
                    </p>
                  ) : null}
                </div>
              </label>
            ))}
          </div>
        )}

        <div>
          <Label htmlFor="reassign-note">Note for the new team (optional)</Label>
          <Textarea
            id="reassign-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="The alert is from the orders database, which your team owns"
            maxLength={500}
            rows={2}
          />
        </div>

        {error && <FormError message={error} />}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || !teamId} data-testid="confirm-reassign">
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            Reassign
          </Button>
        </div>
      </form>
    </Modal>
  );
}
