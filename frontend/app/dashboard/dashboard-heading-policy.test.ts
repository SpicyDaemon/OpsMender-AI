import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const forbiddenHeadingEffects =
  /\b(glitch|chromatic|aberration|scanline|flicker|text-shadow|textShadow|data-glitch)\b/i;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const fullPath = path.join(dir, entry);
    if (statSync(fullPath).isDirectory()) return sourceFiles(fullPath);
    if (/\.(test|spec)\.tsx?$/.test(entry)) return [];
    return /\.(tsx?|css)$/.test(entry) ? [fullPath] : [];
  });
}

describe("dashboard heading visual policy", () => {
  it("keeps glitch/chromatic heading effects out of in-app pages", () => {
    const files = [
      ...sourceFiles(path.join(process.cwd(), "app", "dashboard")),
      path.join(process.cwd(), "components", "ui", "PageHeader.tsx"),
    ];

    for (const file of files) {
      const relative = path.relative(process.cwd(), file);
      expect(readFileSync(file, "utf8"), relative).not.toMatch(forbiddenHeadingEffects);
    }
  });
});
