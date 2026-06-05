/**
 * Compact responder/assignment label for an incident (Part 6).
 *
 * States: awaiting → "Awaiting Alice", assigned → "Assigned to Alice",
 * escalated → "Escalated to Bob", unassigned → "Unassigned". A responder whose
 * user was deleted (id present, name null) renders "Deleted user <id>".
 */

import type { IncidentResponse } from "./types";

export type ResponderTone = "muted" | "warn" | "ok";

export interface ResponderDisplay {
  text: string;
  tone: ResponderTone;
}

function nameFor(uid: string | null | undefined, display: string | null | undefined): string | null {
  if (display) return display;
  if (uid) return `Deleted user ${uid.slice(0, 8)}`;
  return null;
}

export function responderDisplay(inc: IncidentResponse): ResponderDisplay {
  const state = inc.responder_state ?? "unassigned";
  const name = nameFor(inc.responder_user_id, inc.responder_display_name);
  if (state === "unassigned" || !name) return { text: "Unassigned", tone: "muted" };
  if (state === "assigned") return { text: `Assigned to ${name}`, tone: "ok" };
  if (state === "escalated") return { text: `Escalated to ${name}`, tone: "warn" };
  return { text: `Awaiting ${name}`, tone: "warn" };
}

/** Acknowledged-by name (with deleted fallback), or null if not acknowledged. */
export function acknowledgedByName(inc: IncidentResponse): string | null {
  return nameFor(inc.acknowledged_by_user_id, inc.acknowledged_by_display_name);
}
