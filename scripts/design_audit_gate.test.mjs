// Runs design_audit.mjs's own capture and gate code in a VM with controlled
// inputs, so the exit rules are checked without a browser.
// Run: node --test scripts/design_audit_gate.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "design_audit.mjs"), "utf8");
const capture = source.slice(source.indexOf("async function capture("), source.indexOf("function wireConsole("));
const gate = source.slice(source.indexOf("let criticals = 0;"));
assert.ok(capture.startsWith("async function capture(") && gate.includes("process.exit("));

function runGate(results) {
  let exitCode;
  let summary = "";
  const context = vm.createContext({
    results,
    console: { log() {} },
    fs: {
      writeFileSync(file, data) {
        if (String(file).endsWith("summary.txt")) summary = data;
      },
    },
    path,
    OUT: "controlled-design",
    process: {
      exit(code) {
        exitCode = code;
      },
    },
  });
  vm.runInContext(gate, context);
  return { exitCode, summary };
}

// One page through the real capture(); `checks` is what the accessibility
// checker returns, or null to make it throw.
async function captured(checks) {
  let evaluations = 0;
  const page = {
    async evaluate() {
      evaluations += 1;
      if (evaluations === 1) return { scrollH: 844, innerH: 844, scrollW: 390, innerW: 390, title: "Controlled" };
      if (evaluations === 2) return undefined;
      if (checks === null) throw new Error("Injected checker failure");
      return checks;
    },
    async screenshot() {},
    url() {
      return "http://controlled.example.test";
    },
  };
  const context = vm.createContext({
    results: [],
    page,
    path,
    OUT: "controlled-design",
    AXE_SRC: "controlled checker",
    EXPECTED_ERROR_PAGES: new Set(),
    slug: (s) => s,
    console: { log() {} },
    fs: { mkdirSync() {}, writeFileSync() {} },
    process: { stdout: { write() {} }, exit() {} },
  });
  await vm.runInContext(`${capture}; capture(page, "controlled-design", "controlled");`, context);
  return JSON.parse(JSON.stringify(context.results));
}

const notRunPage = { name: "34-people-detail", viewport: "desktop-dark", shot: null, url: null, notRun: "missing record id" };

test("a clean capture passes", async () => {
  assert.equal(runGate(await captured([])).exitCode, 0);
});

test("a checker that throws fails the gate", async () => {
  const { exitCode, summary } = runGate(await captured(null));
  assert.equal(exitCode, 1);
  assert.match(summary, /TOTAL checker failures: +1/);
});

test("a critical violation fails the gate", async () => {
  assert.equal(runGate(await captured([{ id: "controlled-critical", impact: "critical", nodes: 1 }])).exitCode, 1);
});

test("a page that could not be captured fails the gate", async () => {
  const failed = { name: "02-incidents", shot: null, url: "/dashboard/incidents", error: "TimeoutError: page.goto" };
  const { exitCode, summary } = runGate([...(await captured([])), failed]);
  assert.equal(exitCode, 1);
  assert.match(summary, /CAPTURE FAILED: TimeoutError/);
});

test("nothing captured fails the gate", () => {
  assert.equal(runGate([]).exitCode, 1);
  assert.equal(runGate([notRunPage]).exitCode, 1);
});

test("a page without its record is listed as NOT RUN and does not fail a clean run", async () => {
  const { exitCode, summary } = runGate([...(await captured([])), notRunPage]);
  assert.equal(exitCode, 0);
  assert.match(summary, /desktop-dark\/34-people-detail +NOT RUN \(missing record id\)/);
  assert.match(summary, /NOT RUN \(missing record id\): 1/);
});
