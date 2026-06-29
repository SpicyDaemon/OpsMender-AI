/**
 * Roster eligibility rules shared by the rotation-member picker (PagingShell)
 * and the coverage-override picker (RosterCalendarModal).
 *
 * Single source of truth on the frontend; the backend enforces the same rule
 * in `backend/api/routes/paging.py::_validate_roster_eligible_user`.
 */

import type { MultiSelectOption } from "@/components/ui/MultiSelect";
import type { UserResponse } from "@/lib/types";
import { displayName } from "@/lib/users";

// A user may sit on a rotation (or cover one) only when they are an active,
// non-deleted Admin/Operator who also belongs to the roster's owning team.
// `teamMemberIds` is the set of user_ids on the selected team (null while it is
// still loading, which yields no options so we never show unscoped users).
export function eligibleRosterMemberOptions(
  users: UserResponse[],
  teamMemberIds: Set<string> | null,
): MultiSelectOption[] {
  return users
    .filter(
      (u) =>
        u.is_active &&
        !u.deleted_at &&
        (u.role === "admin" || u.role === "operator") &&
        (teamMemberIds?.has(u.id) ?? false),
    )
    .map((u) => ({
      value: u.id,
      label: displayName(u),
      sublabel: `${u.email} · ${u.role}`,
    }));
}

// Keep only the selected members who belong to the (new) team — used to
// reconcile the picker when the team changes.
export function keepRosterMembersOnTeam(
  selected: string[],
  teamMemberIds: Set<string>,
): string[] {
  return selected.filter((id) => teamMemberIds.has(id));
}
