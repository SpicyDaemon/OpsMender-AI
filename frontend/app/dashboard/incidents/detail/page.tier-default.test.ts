import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const incidentDetailSource = readFileSync(
  join(process.cwd(), "app", "dashboard", "incidents", "detail", "page.tsx"),
  "utf8",
);

// M1-84 (owner decision O-26): a new session asks a person to approve each
// action by default instead of running in advisory mode.
describe("start session dialog default tier", () => {
  it("opens on Tier 1, approval required", () => {
    const formInit = incidentDetailSource.indexOf("incident_id: incidentId,");
    expect(formInit).toBeGreaterThan(-1);
    const window = incidentDetailSource.slice(formInit, formInit + 200);
    expect(window).toContain("tier: 1,");
    expect(window).not.toContain("tier: 2,");
  });

  it("says so in its help text", () => {
    expect(incidentDetailSource).toContain("Tier 1 (Approval Required) is the default");
    expect(incidentDetailSource).not.toContain("Tier 2 (Advisory) is the default");
  });
});
