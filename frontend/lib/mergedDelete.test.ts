import { describe, expect, it } from "vitest";

import { mergedDeleteNote } from "@/lib/mergedDelete";

describe("mergedDeleteNote", () => {
  it("adds nothing when no incident was combined", () => {
    expect(mergedDeleteNote([])).toBe("");
  });

  it("names one combined incident", () => {
    expect(mergedDeleteNote(["Disk alert"])).toBe(
      ' This also deletes the 1 incident combined into it: "Disk alert".',
    );
  });

  it("names three and counts the rest", () => {
    expect(mergedDeleteNote(["A", "B", "C", "D", "E"])).toBe(
      ' This also deletes the 5 incidents combined into it: "A", "B", "C" and 2 more.',
    );
  });

  it("speaks of them when several incidents are selected", () => {
    expect(mergedDeleteNote(["A", "B"], 2)).toBe(
      ' This also deletes the 2 incidents combined into them: "A", "B".',
    );
  });
});
