/**
 * Human-facing display name for a user.
 *
 * Prefers "First Last" (from the user's profile), falling back to the username
 * when no name is set. Used wherever we surface a person in the UI (team
 * members, roster rotations, escalation targets) so operators see real names,
 * not login handles.
 */

export function displayName(
  user:
    | {
        first_name?: string | null;
        last_name?: string | null;
        username?: string | null;
      }
    | null
    | undefined,
): string {
  if (!user) return "";
  const full = [user.first_name, user.last_name]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" ");
  return full || user.username || "";
}
