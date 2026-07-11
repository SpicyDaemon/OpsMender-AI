import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const sessionDetailSource = readFileSync(
  join(process.cwd(), "app", "dashboard", "sessions", "detail", "page.tsx"),
  "utf8",
);

describe("session detail model picker", () => {
  it("renders only backend-allowed models and switches through the enforced endpoint", () => {
    expect(sessionDetailSource).toContain("allowed_model_config_ids");
    expect(sessionDetailSource).toContain("switchSessionModel");
    expect(sessionDetailSource).toContain("allowedModelOptions.map");
    expect(sessionDetailSource).toContain(" · default");
  });
});

describe("session detail activity layout", () => {
  it("keeps the event and chat work area visible when context panels grow", () => {
    expect(sessionDetailSource).toContain("min-h-[32rem]");
    expect(sessionDetailSource).toContain("lg:min-h-[36rem]");
  });
});
