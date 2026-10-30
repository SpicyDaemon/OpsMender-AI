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

describe("session detail model summary", () => {
  it("labels the closing text as the model's summary, not a record", () => {
    expect(sessionDetailSource).toContain("Model summary");
    expect(sessionDetailSource).toContain("the record, not the prose, says what happened");
  });
});

describe("session detail activity layout", () => {
  it("keeps the event and chat work area visible when context panels grow", () => {
    expect(sessionDetailSource).toContain("min-h-[32rem]");
    expect(sessionDetailSource).toContain("lg:min-h-[36rem]");
  });
});

describe("session detail approval events", () => {
  it("names a live approval by the id the server sends, never undefined (F2)", () => {
    const sites = sessionDetailSource.match(/msg\.data\.approval_id \?\? msg\.data\.id \?\? ""/g) ?? [];
    expect(sites).toHaveLength(2);
    expect(sessionDetailSource).not.toContain("`ID: ${msg.data.approval_id}`");
  });
});
