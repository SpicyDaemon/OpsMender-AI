/**
 * Roster coverage-override picker — team scoping + copy.
 *
 * v1 follow-up: overrides must be limited to the same eligible Admin/Operator
 * members of the roster's team as rotation members. The picker reuses
 * `eligibleRosterMemberOptions`, so these tests assert that helper behaves
 * correctly for the override case and that the override modal copy is present.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { eligibleRosterMemberOptions } from "@/lib/rosterEligibility";
import type { UserResponse } from "@/lib/types";

function user(partial: Partial<UserResponse> & { id: string }): UserResponse {
  return {
    username: partial.id,
    email: `${partial.id}@test.com`,
    role: "operator",
    is_active: true,
    auth_source: "local",
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  } as UserResponse;
}

const opA = user({ id: "opA", role: "operator" });
const adminA = user({ id: "adminA", role: "admin" });
const viewerA = user({ id: "viewerA", role: "viewer" });
const inactiveA = user({ id: "inactiveA", is_active: false });
const deletedA = user({ id: "deletedA", deleted_at: "2026-02-01T00:00:00Z" });
const opB = user({ id: "opB" });

const allUsers = [opA, adminA, viewerA, inactiveA, deletedA, opB];
// Roster's team (Team A) holds everyone except opB.
const teamAIds = new Set(["opA", "adminA", "viewerA", "inactiveA", "deletedA"]);

describe("override picker eligibility (roster team scope)", () => {
  it("offers only the roster team's Admin/Operator users", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value).sort()).toEqual(["adminA", "opA"]);
  });

  it("excludes users from another team", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value)).not.toContain("opB");
  });

  it("excludes viewers", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value)).not.toContain("viewerA");
  });

  it("excludes inactive and deleted users", () => {
    const values = eligibleRosterMemberOptions(allUsers, teamAIds).map(
      (o) => o.value,
    );
    expect(values).not.toContain("inactiveA");
    expect(values).not.toContain("deletedA");
  });

  it("is empty when the team has no eligible members", () => {
    expect(eligibleRosterMemberOptions(allUsers, new Set(["viewerA"]))).toEqual(
      [],
    );
  });
});

describe("override modal copy", () => {
  const source = readFileSync(
    join(process.cwd(), "components", "RosterCalendarModal.tsx"),
    "utf8",
  );

  it("explains overrides are limited to the roster team's Admin/Operator users", () => {
    expect(source).toContain("can be used for overrides.");
  });

  it("shows an empty state when no eligible team members exist", () => {
    expect(source).toContain(
      "No eligible team members. Add Admin or Operator users to this",
    );
  });

  it("scopes the override picker to the roster team's members", () => {
    // The modal loads the roster team's membership and feeds it to the shared
    // eligibility helper rather than listing all workspace users.
    expect(source).toContain("listTeamMembers(roster.team_id)");
    expect(source).toContain("eligibleRosterMemberOptions(users, teamMemberIds)");
  });
});
