import { describe, expect, it } from "vitest";

import { rosterImpactNote } from "@/lib/rosterImpact";

describe("rosterImpactNote", () => {
  it("adds nothing for someone on no Roster", () => {
    expect(rosterImpactNote([])).toBe("");
  });

  it("names the Rosters and who takes the current shift", () => {
    expect(
      rosterImpactNote([
        {
          roster_id: "r1",
          roster_name: "Primary",
          on_current_shift: true,
          current_shift_taken_by: "grace",
        },
        {
          roster_id: "r2",
          roster_name: "Secondary",
          on_current_shift: false,
          current_shift_taken_by: null,
        },
      ]),
    ).toBe(
      " They leave these Rosters: Primary (grace takes the current shift), Secondary. The others keep their shifts.",
    );
  });

  it("says so when nobody else can take the shift", () => {
    expect(
      rosterImpactNote([
        {
          roster_id: "r1",
          roster_name: "Solo",
          on_current_shift: true,
          current_shift_taken_by: null,
        },
      ]),
    ).toBe(
      " They leave this Roster: Solo (nobody else can take the current shift). The others keep their shifts.",
    );
  });
});
