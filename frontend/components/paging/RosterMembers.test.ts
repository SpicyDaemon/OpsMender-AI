/**
 * Roster rotation-member eligibility + team reconciliation.
 *
 * v1 QA bug: the roster member picker showed every Admin/Operator in the
 * workspace, ignoring the selected team. These tests lock the team-scoped
 * filtering and the team-change reconciliation, plus the modal copy.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  eligibleRosterMemberOptions,
  keepRosterMembersOnTeam,
} from "@/lib/rosterEligibility";
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

// Team A: opA (operator), adminA (admin), viewerA (viewer), inactiveA, deletedA
// Team B: opB (operator)
const opA = user({ id: "opA", role: "operator" });
const adminA = user({ id: "adminA", role: "admin" });
const viewerA = user({ id: "viewerA", role: "viewer" });
const inactiveA = user({ id: "inactiveA", role: "operator", is_active: false });
const deletedA = user({
  id: "deletedA",
  role: "operator",
  deleted_at: "2026-02-01T00:00:00Z",
});
const opB = user({ id: "opB", role: "operator" });

const allUsers = [opA, adminA, viewerA, inactiveA, deletedA, opB];
const teamAIds = new Set(["opA", "adminA", "viewerA", "inactiveA", "deletedA"]);
const teamBIds = new Set(["opB"]);

describe("eligibleRosterMemberOptions", () => {
  it("returns nothing before a team is loaded (null)", () => {
    expect(eligibleRosterMemberOptions(allUsers, null)).toEqual([]);
  });

  it("returns only Team A's Admin/Operator users", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value).sort()).toEqual(["adminA", "opA"]);
  });

  it("excludes users from another team", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value)).not.toContain("opB");
  });

  it("excludes viewers even when they are on the team", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    expect(opts.map((o) => o.value)).not.toContain("viewerA");
  });

  it("excludes inactive and deleted users", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamAIds);
    const values = opts.map((o) => o.value);
    expect(values).not.toContain("inactiveA");
    expect(values).not.toContain("deletedA");
  });

  it("scopes to Team B when Team B is selected", () => {
    const opts = eligibleRosterMemberOptions(allUsers, teamBIds);
    expect(opts.map((o) => o.value)).toEqual(["opB"]);
  });

  it("is empty when the team has no eligible members", () => {
    const onlyViewer = new Set(["viewerA"]);
    expect(eligibleRosterMemberOptions(allUsers, onlyViewer)).toEqual([]);
  });
});

describe("keepRosterMembersOnTeam", () => {
  it("drops selected members who are not on the new team", () => {
    // opA was selected for Team A; switching to Team B should drop it.
    expect(keepRosterMembersOnTeam(["opA", "adminA"], teamBIds)).toEqual([]);
  });

  it("keeps members who are still valid on the new team", () => {
    expect(keepRosterMembersOnTeam(["opA", "opB"], teamAIds)).toEqual(["opA"]);
  });
});

describe("roster modal copy", () => {
  const source = readFileSync(
    join(process.cwd(), "components", "paging", "PagingShell.tsx"),
    "utf8",
  );

  it("prompts to pick a team first", () => {
    expect(source).toContain("Select a team first to choose rotation members.");
  });

  it("shows the no-eligible-members empty state", () => {
    expect(source).toContain(
      "No eligible team members. Add Admin or Operator users to this team",
    );
  });

  it("explains only selected-team Admin/Operator users are eligible", () => {
    expect(source).toContain(
      "Only Admin and Operator users assigned to the selected team can be",
    );
  });

  it("warns when members are removed on team change", () => {
    expect(source).toContain(
      "Some selected members were removed because they are not part of the selected team.",
    );
  });
});
