export const STALE_ACTIVE_SESSION_MS = 60 * 60 * 1000;

export function isStaleActiveSession(
  session: { started_at: string | null },
  nowMs = Date.now(),
): boolean {
  if (!session.started_at) return false;
  const startedAt = Date.parse(session.started_at);
  return Number.isFinite(startedAt) && startedAt < nowMs - STALE_ACTIVE_SESSION_MS;
}
