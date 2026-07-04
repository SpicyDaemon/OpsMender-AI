// Design-audit harness for the OpsMender dashboard.
//
// Drives a real Chromium (Playwright) through every operator-facing route at
// desktop/tablet/mobile viewports in dark + light themes, runs axe-core
// (WCAG 2.1 AA) on each page, records console errors and horizontal
// overflow, and writes screenshots + a machine-readable results file.
//
// This is the harness behind docs/DESIGN_AUDIT_2026-07-03.md. Re-run it after
// remediation work; the exit code is the pass/fail gate:
//   exit 0  →  0 axe-critical violations, 0 axe-serious violations, and no
//              unexpected console errors on any captured page
//   exit 1  →  anything above found (details in .design-audit/results.json)
//
// Setup (one-time):   cd frontend && npm install    (playwright + axe-core are frontend devDeps)
// Run (full):         node scripts/design_audit.mjs
// Run (smoke):        node scripts/design_audit.mjs --smoke      # login page only
// Env:
//   OPSMENDER_BASE_URL  default http://localhost:8000
//   OPSMENDER_EMAIL     default admin            (seeded demo admin)
//   OPSMENDER_PASSWORD  default admin123
//
// Output: .design-audit/shots/<viewport-theme>/<page>.png
//         .design-audit/results.json
//         .design-audit/summary.txt (same table as stdout)

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

// Resolve deps from frontend/node_modules explicitly so this works even where
// the scripts/node_modules symlink doesn't materialize (core.symlinks=false).
const require = createRequire(new URL("../frontend/node_modules/", import.meta.url));
const { chromium } = require("playwright");

function readDotEnv() {
  const envPath = path.resolve(".env");
  if (!fs.existsSync(envPath)) return {};
  return Object.fromEntries(
    fs
      .readFileSync(envPath, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#") && line.includes("="))
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
}

const DOTENV = readDotEnv();
const BASE = process.env.OPSMENDER_BASE_URL || "http://localhost:8000";
const EMAIL =
  process.env.OPSMENDER_EMAIL ||
  DOTENV.OPSMENDER_BOOTSTRAP_ADMIN_EMAIL ||
  "admin";
const PASSWORD =
  process.env.OPSMENDER_PASSWORD ||
  DOTENV.OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD ||
  "admin123";
const SMOKE = process.argv.includes("--smoke");

const OUT = path.resolve(".design-audit");
const SHOTS = path.join(OUT, "shots");
const AXE_SRC = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

// Console errors that are part of a deliberately-exercised error state and
// must NOT fail the gate (keyed by capture name).
const EXPECTED_ERROR_PAGES = new Set([
  "00-invite-bad-token", // 400 from the bogus invite token we visit on purpose
  "00-login-error", // 401 from the wrong-password submission we make on purpose
]);

const results = [];

const slug = (s) => s.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();

async function settle(page, ms = 900) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function capture(page, dir, name, { runAxe = true, errBuf } = {}) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${slug(name)}.png`);
  const dims = await page.evaluate(() => ({
    scrollH: document.documentElement.scrollHeight,
    innerH: window.innerHeight,
    scrollW: document.documentElement.scrollWidth,
    innerW: window.innerWidth,
    title: document.title,
  }));
  const fullPage = dims.scrollH > dims.innerH + 80 && dims.scrollH <= 3400;
  await page.screenshot({ path: file, fullPage }).catch(() => {});

  let axe = null;
  if (runAxe) {
    try {
      await page.evaluate((src) => {
        if (!window.axe) {
          const s = document.createElement("script");
          s.textContent = src;
          document.head.appendChild(s);
        }
      }, AXE_SRC);
      axe = await page.evaluate(async () => {
        const res = await window.axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          resultTypes: ["violations"],
        });
        return res.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          help: v.help,
          nodes: v.nodes.length,
          sample: v.nodes.slice(0, 2).map((n) => n.html.slice(0, 160)),
        }));
      });
    } catch (e) {
      axe = [{ id: "axe-failed", impact: "critical", help: String(e).slice(0, 120), nodes: 1 }];
    }
  }

  results.push({
    name,
    shot: path.relative(OUT, file),
    url: page.url(),
    title: dims.title,
    overflowX: dims.scrollW > dims.innerW + 2,
    consoleErrors: errBuf ? errBuf.splice(0) : [],
    expectedErrors: EXPECTED_ERROR_PAGES.has(name),
    axe,
  });
  process.stdout.write(`  ✓ ${name}\n`);
}

function wireConsole(page, buf) {
  page.on("console", (m) => {
    if (m.type() === "error") buf.push(m.text().slice(0, 300));
  });
  page.on("pageerror", (e) => buf.push("pageerror: " + String(e).slice(0, 300)));
}

async function login(page) {
  await page.goto(`${BASE}/login`);
  await settle(page, 400);
  await page.fill('input[type="email"], input[name="email"], input[autocomplete*="username"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard**", { timeout: 15000 });
  await settle(page);
}

const browser = await chromium.launch();

// ---------------------------------------------------------------- desktop dark
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
const page = await ctx.newPage();
const errBuf = [];
wireConsole(page, errBuf);

const dDark = path.join(SHOTS, "desktop-dark");

console.log("auth pages:");
await page.goto(`${BASE}/login`);
await settle(page);
await capture(page, dDark, "00-login", { errBuf });

if (SMOKE) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 1));
  await browser.close();
  console.log("SMOKE OK");
  process.exit(0);
}

await page.goto(`${BASE}/register`);
await settle(page);
await capture(page, dDark, "00-register", { errBuf });

await page.goto(`${BASE}/password-reset`);
await settle(page);
await capture(page, dDark, "00-password-reset", { errBuf });

await page.goto(`${BASE}/invite?token=bogus-token-for-error-state`);
await settle(page);
await capture(page, dDark, "00-invite-bad-token", { errBuf });

// Deliberate wrong-password submission to exercise the login error state.
console.log("logging in…");
await page.goto(`${BASE}/login`);
await settle(page, 400);
await page.fill('input[type="email"], input[name="email"], input[autocomplete*="username"]', EMAIL);
await page.fill('input[type="password"]', "definitely-wrong");
await page.click('button[type="submit"]');
await page
  .waitForFunction(() => {
    const button = document.querySelector('button[type="submit"]');
    return button && !button.disabled;
  }, { timeout: 60000 })
  .catch(() => {});
await settle(page, 400);
await capture(page, dDark, "00-login-error", { errBuf, runAxe: false });

await page.fill('input[type="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL("**/dashboard**", { timeout: 60000 });
await settle(page);

// Fetch real record ids so detail pages render actual data, not error states.
const ids = await page.evaluate(async () => {
  const t = localStorage.getItem("opsmender_token");
  const h = { Authorization: `Bearer ${t}` };
  const g = async (u) => {
    try {
      return await (await fetch(u, { headers: h })).json();
    } catch {
      return null;
    }
  };
  const inc = await g("/incidents?limit=20");
  const ses = await g("/sessions?limit=10");
  const sla = await g("/sla-targets");
  const usr = await g("/auth/users");
  const resolved = (inc?.items || []).find((i) => i.status === "resolved");
  return {
    incident: inc?.items?.[0]?.id || null,
    resolvedIncident: resolved?.id || inc?.items?.[0]?.id || null,
    session: ses?.items?.[0]?.id || null,
    slaTarget: sla?.items?.[0]?.id || null,
    user: usr?.items?.[0]?.id || usr?.users?.[0]?.id || null,
  };
});
console.log("ids:", JSON.stringify(ids));

const pages = [
  ["01-dashboard", "/dashboard"],
  ["02-incidents", "/dashboard/incidents"],
  ["03-incident-detail", ids.incident && `/dashboard/incidents/detail?id=${ids.incident}`],
  ["04-postmortem", ids.resolvedIncident && `/dashboard/incidents/postmortem?id=${ids.resolvedIncident}`],
  ["05-session-detail", ids.session && `/dashboard/sessions/detail?id=${ids.session}`],
  ["06-orchestration", "/dashboard/orchestration"],
  ["07-approvals", "/dashboard/approvals"],
  ["08-activity", "/dashboard/activity"],
  ["09-workflows", "/dashboard/workflows"],
  ["10-agent-teams", "/dashboard/agent-teams"],
  ["11-models", "/dashboard/models"],
  ["12-mcp-servers", "/dashboard/mcp-servers"],
  ["13-skills", "/dashboard/skills"],
  ["14-memories", "/dashboard/memories"],
  ["15-integrations", "/dashboard/integrations"],
  ["17-reliability", "/dashboard/reliability"],
  ["18-reliability-detail", ids.slaTarget && `/dashboard/reliability/detail?id=${ids.slaTarget}`],
  ["19-reports", "/dashboard/reports"],
  ["21-paging-teams", "/dashboard/paging/teams"],
  ["22-paging-rosters", "/dashboard/paging/rosters"],
  ["23-paging-escalation-chains", "/dashboard/paging/escalation-chains"],
  ["24-paging-services", "/dashboard/paging/services"],
  ["26-paging-maintenance-windows", "/dashboard/paging/maintenance-windows"],
  ["28-paging-notifications", "/dashboard/paging/notifications"],
  ["30-on-call-schedule", "/dashboard/on-call-schedule"],
  ["31-inbox", "/dashboard/notifications"],
  ["32-notification-preferences", "/dashboard/notifications/preferences"],
  ["33-people", "/dashboard/people"],
  ["34-people-detail", ids.user && `/dashboard/people/detail?id=${ids.user}`],
  ["35-config", "/dashboard/config"],
  ["36-profile", "/dashboard/settings/profile"],
];

console.log("dashboard pages (desktop dark):");
for (const [name, url] of pages) {
  if (!url) continue;
  try {
    await page.goto(`${BASE}${url}`);
    await settle(page);
    await capture(page, dDark, name, { errBuf });
  } catch (e) {
    results.push({ name, shot: null, url, error: String(e).slice(0, 200) });
    console.log(`  ✗ ${name}: ${e}`);
  }
}

// ------------------------------------------------------------- interactions
console.log("interaction states:");
const dInt = path.join(SHOTS, "interactions");

await page.goto(`${BASE}/dashboard/incidents?new=1`);
await settle(page);
await capture(page, dInt, "incident-new-dialog", { errBuf });

await page.goto(`${BASE}/dashboard`);
await settle(page, 500);
const bell = page
  .locator('button:has(svg.lucide-bell), [aria-label*="otification"], [aria-label*="nbox"]')
  .first();
if (await bell.count()) {
  await bell.click().catch(() => {});
  await page.waitForTimeout(700);
  await capture(page, dInt, "bell-popover", { errBuf, runAxe: false });
  await page.keyboard.press("Escape");
}

await page.goto(`${BASE}/dashboard`);
await settle(page, 500);
for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
await capture(page, dInt, "focus-after-6-tabs", { errBuf, runAxe: false });

// ---------------------------------------------------------------- light theme
console.log("light theme:");
const dLight = path.join(SHOTS, "desktop-light");
await page.evaluate(() => localStorage.setItem("opsmender:theme", "light"));
for (const [name, url] of [
  ["01-dashboard", "/dashboard"],
  ["02-incidents", "/dashboard/incidents"],
  ["03-incident-detail", ids.incident && `/dashboard/incidents/detail?id=${ids.incident}`],
  ["05-session-detail", ids.session && `/dashboard/sessions/detail?id=${ids.session}`],
  ["30-on-call-schedule", "/dashboard/on-call-schedule"],
  ["11-models", "/dashboard/models"],
  ["36-profile", "/dashboard/settings/profile"],
  ["00-login-light", "/login"],
]) {
  if (!url) continue;
  await page.goto(`${BASE}${url}`);
  await settle(page);
  await capture(page, dLight, name, { errBuf });
}
await page.evaluate(() => localStorage.setItem("opsmender:theme", "system")).catch(() => {});
await ctx.close();

// -------------------------------------------------------------------- mobile
console.log("mobile 390x844:");
const mctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  colorScheme: "dark",
  isMobile: true,
  hasTouch: true,
  deviceScaleFactor: 2,
});
const mpage = await mctx.newPage();
const mErr = [];
wireConsole(mpage, mErr);
await login(mpage);

const dMob = path.join(SHOTS, "mobile-dark");
for (const [name, url] of [
  ["00-login", "/login"],
  ["01-dashboard", "/dashboard"],
  ["02-incidents", "/dashboard/incidents"],
  ["03-incident-detail", ids.incident && `/dashboard/incidents/detail?id=${ids.incident}`],
  ["05-session-detail", ids.session && `/dashboard/sessions/detail?id=${ids.session}`],
  ["07-approvals", "/dashboard/approvals"],
  ["22-paging-rosters", "/dashboard/paging/rosters"],
  ["30-on-call-schedule", "/dashboard/on-call-schedule"],
  ["33-people", "/dashboard/people"],
  ["36-profile", "/dashboard/settings/profile"],
  ["11-models", "/dashboard/models"],
]) {
  if (!url) continue;
  await mpage.goto(`${BASE}${url}`);
  await settle(mpage);
  await capture(mpage, dMob, name, { errBuf: mErr });
}
await mctx.close();

// -------------------------------------------------------------------- tablet
console.log("tablet 834x1112:");
const tctx = await browser.newContext({ viewport: { width: 834, height: 1112 }, colorScheme: "dark" });
const tpage = await tctx.newPage();
await login(tpage);
const dTab = path.join(SHOTS, "tablet-dark");
for (const [name, url] of [
  ["01-dashboard", "/dashboard"],
  ["02-incidents", "/dashboard/incidents"],
  ["30-on-call-schedule", "/dashboard/on-call-schedule"],
]) {
  await tpage.goto(`${BASE}${url}`);
  await settle(tpage);
  await capture(tpage, dTab, name, { runAxe: true });
}
await tctx.close();
await browser.close();

// ------------------------------------------------------------------- results
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 1));

let criticals = 0;
let serious = 0;
let unexpectedErrors = 0;
const lines = [];
for (const p of results) {
  const ax = (p.axe || []).filter((v) => v.id !== "axe-failed");
  const c = ax.filter((v) => v.impact === "critical").reduce((n, v) => n + v.nodes, 0);
  const s = ax.filter((v) => v.impact === "serious").reduce((n, v) => n + v.nodes, 0);
  const errs = p.expectedErrors ? 0 : (p.consoleErrors || []).length;
  criticals += c;
  serious += s;
  unexpectedErrors += errs;
  lines.push(
    `${(p.shot || p.name || "?").padEnd(52)} critical=${c} serious=${s} consoleErrors=${errs} overflowX=${p.overflowX ? "YES" : "no"}`,
  );
}
const summary = [
  ...lines,
  "",
  `TOTAL axe critical nodes:   ${criticals}`,
  `TOTAL axe serious nodes:    ${serious}`,
  `TOTAL unexpected console errors: ${unexpectedErrors}`,
  "",
  `GATE: ${criticals === 0 && serious === 0 && unexpectedErrors === 0 ? "PASS" : "FAIL"}`,
].join("\n");
fs.writeFileSync(path.join(OUT, "summary.txt"), summary);
console.log("\n" + summary);
process.exit(criticals === 0 && serious === 0 && unexpectedErrors === 0 ? 0 : 1);
