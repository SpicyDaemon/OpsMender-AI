import { describe, expect, it } from "vitest";
import { personColor } from "./calendarColor";

describe("personColor", () => {
  it("is deterministic for the same user id", () => {
    expect(personColor("user-123")).toBe(personColor("user-123"));
  });

  it("returns a neutral class for no user (gap/unassigned)", () => {
    const neutral = personColor(null);
    expect(neutral).toContain("text-fg-muted");
    expect(personColor(undefined)).toBe(neutral);
  });

  it("returns a colored class for a real user", () => {
    expect(personColor("abc")).not.toContain("text-fg-muted");
  });
});
