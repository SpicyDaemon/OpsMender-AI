/**
 * What an approval request's action record says about the call it is for.
 *
 * A session's action and a workflow step name the tool as `tool_name`: that
 * is the tool the tier check gates and the session runs. The model writes a
 * session's record and can add other keys to it, so the heading a page gives
 * a request comes from `tool_name` whenever the record has one (M1-173). The
 * keys `tool`, `name` and `action` are older shapes and are read only for a
 * record without it, as the Approvals page always read them: the first of
 * the three that is there, if it is text.
 */

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function record(action: unknown): Record<string, unknown> | null {
  return action && typeof action === "object" && !Array.isArray(action)
    ? (action as Record<string, unknown>)
    : null;
}

/** Whether the record names its tool as `tool_name`, as a session's action
 * and a workflow step do. */
export function namesItsTool(action: unknown): boolean {
  const held = record(action);
  return held !== null && held.tool_name !== undefined && held.tool_name !== null;
}

/** The tool the request is for, or null when the record names none. */
export function approvalToolName(action: unknown): string | null {
  const held = record(action);
  if (held === null) return null;
  if (namesItsTool(held)) return text(held.tool_name);
  return text(held.tool ?? held.name ?? held.action);
}
