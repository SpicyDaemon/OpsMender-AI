// Curated README and site screenshot capture for OpsMender.
//
// Run against a fresh database created by scripts/seed_demo.py:
//   OPSMENDER_DEMO_OPERATOR_PASSWORD='<seeded demo password>' node scripts/take_screenshots.mjs
//
// Env:
//   OPSMENDER_BASE_URL       default http://localhost:8000
//   OPSMENDER_EMAIL          login override
//   OPSMENDER_PASSWORD       login override
//   OPSMENDER_DEMO_OPERATOR_PASSWORD  seeded operator login (gallery capture)
//   OPSMENDER_SCREENSHOT_DIR default site/public/screenshots
//
// Desktop captures are 1440x900 at 2x (2880x1800). Phone captures use two
// device profiles (a: 393 wide at 3x, b: 412 wide at 2.625x) so the site can
// show the app in a phone browser.
//
// The script never starts an AI session. It captures only deterministic seeded
// scenarios and restores the workspace tier even when a capture fails.

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(new URL("../frontend/node_modules/", import.meta.url));
const { chromium, devices } = require("playwright");

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
        return [line.slice(0, i), line.slice(i + 1).replace(/^['"]|['"]$/g, "")];
      }),
  );
}

const DOTENV = readDotEnv();
const BASE = process.env.OPSMENDER_BASE_URL || "http://localhost:8000";
const EMAIL =
  process.env.OPSMENDER_EMAIL ||
  process.env.OPSMENDER_BOOTSTRAP_ADMIN_EMAIL ||
  DOTENV.OPSMENDER_BOOTSTRAP_ADMIN_EMAIL;
const PASSWORD =
  process.env.OPSMENDER_PASSWORD ||
  process.env.OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD ||
  DOTENV.OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD;
const OPERATOR_PASSWORD = process.env.OPSMENDER_DEMO_OPERATOR_PASSWORD;
const OUT = path.resolve(process.env.OPSMENDER_SCREENSHOT_DIR || "site/public/screenshots");

if (!EMAIL || !PASSWORD) {
  throw new Error(
    "Set OPSMENDER_EMAIL/OPSMENDER_PASSWORD or OPSMENDER_BOOTSTRAP_ADMIN_* in the environment or .env.",
  );
}
if (!OPERATOR_PASSWORD) {
  throw new Error("Set OPSMENDER_DEMO_OPERATOR_PASSWORD for the seeded operator gallery capture.");
}

const VIEWPORT = { width: 1440, height: 900 };
const SCALE = 2;
const SCENARIO_IDS = ["demo-tier-0", "demo-tier-1", "demo-tier-2"];
const FORBIDDEN_TEXT = ["invalid x-api-key", "no events were recorded", "session failed", "failed to load session"];
const PHONES = {
  a: { ...devices["iPhone 15 Pro"], defaultBrowserType: undefined },
  b: { ...devices["Pixel 7"], defaultBrowserType: undefined },
};

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: "dark", deviceScaleFactor: SCALE });
let page = await context.newPage();

let responseFailures = [];
let pageErrors = [];
function monitor(target) {
  target.on("response", (response) => {
    const status = response.status();
    if (status === 401 || status === 403 || status >= 500) {
      responseFailures.push(`${status} ${response.request().method()} ${response.url()}`);
    }
  });
  target.on("pageerror", (error) => pageErrors.push(error.message));
}
monitor(page);

async function settle(ms = 900) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function requestJson(method, url, token, data) {
  const options = { method, headers: { Authorization: `Bearer ${token}` } };
  if (data !== undefined) options.data = data;
  const response = await page.request.fetch(`${BASE}${url}`, options);
  if (!response.ok()) throw new Error(`${method} ${url} failed: ${response.status()} ${await response.text()}`);
  const body = await response.text();
  return body ? JSON.parse(body) : null;
}

async function tokenFor(username, password) {
  const response = await page.request.post(`${BASE}/auth/login`, { data: { username, password } });
  if (!response.ok()) throw new Error(`Login failed for ${username}: ${response.status()}`);
  return (await response.json()).access_token;
}

async function login() {
  const token = await tokenFor(EMAIL, PASSWORD);
  await page.addInitScript((accessToken) => {
    localStorage.setItem("opsmender_token", accessToken);
    localStorage.setItem("opsmender:theme", "dark");
  }, token);
  return token;
}

async function loadScenarioState(token) {
  const [incidentResponse, sessionResponse, approvalResponse] = await Promise.all([
    requestJson("GET", "/incidents?limit=200", token),
    requestJson("GET", "/sessions?limit=100", token),
    requestJson("GET", "/approvals?status=pending&limit=100", token),
  ]);
  const incidents = new Map(incidentResponse.items.filter((i) => SCENARIO_IDS.includes(i.external_id)).map((i) => [i.external_id, i]));
  const sessionsByIncident = new Map(sessionResponse.items.map((s) => [s.incident_id, s]));
  const scenarios = {};
  for (let tier = 0; tier <= 2; tier += 1) {
    const incident = incidents.get(`demo-tier-${tier}`);
    const session = incident ? sessionsByIncident.get(incident.id) : null;
    if (!incident || !session || session.tier !== tier) throw new Error(`Fresh demo seed is required: missing Tier ${tier} incident/session.`);
    scenarios[tier] = { incident, session };
  }
  if (!approvalResponse.items.find((a) => a.session_id === scenarios[1].session.id)) throw new Error("Fresh demo seed is required: Tier 1 pending approval is missing.");
  return scenarios;
}

async function setWorkspaceTier(token, tier) {
  const config = await requestJson("PUT", "/config", token, { tier });
  if (config.tier !== tier) throw new Error(`Workspace tier remained ${config.tier}; expected ${tier}.`);
}

async function assertCleanCapture(label, requireEvents) {
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  const forbidden = FORBIDDEN_TEXT.find((text) => bodyText.includes(text));
  if (forbidden) throw new Error(`${label} contains forbidden state: ${forbidden}`);
  if (/qa-\d{10,}/i.test(bodyText)) throw new Error(`${label} contains test-run debris; use a fresh demo seed.`);
  const addresses = bodyText.match(/[a-z0-9._%+-]+@(?:[a-z0-9.-]+\.[a-z]{2,}|localhost)/g) ?? [];
  if (addresses.some((a) => !a.endsWith("@example.com") && !a.endsWith("@localhost"))) throw new Error(`${label} contains an email outside the reserved demo domains.`);
  const toasts = await page.locator(".ops-toast").allInnerTexts();
  if (toasts.length > 0) throw new Error(`${label} rendered a toast: ${toasts.join(" | ")}`);
  if (responseFailures.length > 0) throw new Error(`${label} made forbidden requests:\n${responseFailures.join("\n")}`);
  if (pageErrors.length > 0) throw new Error(`${label} raised page errors:\n${pageErrors.join("\n")}`);
  if (requireEvents) {
    const eventLog = page.locator('[role="log"][aria-label="Session event stream"]');
    await eventLog.waitFor({ state: "visible", timeout: 15000 });
    const eventText = (await eventLog.innerText()).trim();
    if (!eventText || /waiting for events/i.test(eventText)) throw new Error(`${label} has an empty session event stream.`);
  }
}

async function capture({ file, route, label, requireEvents = false, focusEvents = false }) {
  responseFailures = [];
  pageErrors = [];
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await settle();
  await page.evaluate(() => { window.scrollTo(0, 0); document.getElementById("main-content")?.scrollTo(0, 0); });
  if (focusEvents) {
    const eventLog = page.locator('[role="log"][aria-label="Session event stream"]');
    await eventLog.waitFor({ state: "visible", timeout: 15000 });
    await eventLog.evaluate((element) => {
      const main = element.closest("main");
      const panel = element.parentElement;
      if (!main || !panel) return;
      const offset = panel.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
      main.scrollTo({ top: Math.max(0, offset - 170), behavior: "instant" });
    });
  }
  await page.waitForTimeout(150);
  await assertCleanCapture(label, requireEvents);
  const output = path.join(OUT, file);
  await page.screenshot({ path: output, animations: "disabled" });
  console.log(`OK ${label}: ${file}`);
}

// Phone captures reuse the admin token in a device-emulating context.
async function capturePhones(token, scenarios) {
  const adminPage = page;
  for (const [key, device] of Object.entries(PHONES)) {
    const phoneContext = await browser.newContext({ ...device, colorScheme: "dark" });
    try {
      page = await phoneContext.newPage();
      monitor(page);
      await page.addInitScript((accessToken) => {
        localStorage.setItem("opsmender_token", accessToken);
        localStorage.setItem("opsmender:theme", "dark");
      }, token);
      await capture({ file: `phone-${key}-incidents.png`, route: "/dashboard/incidents", label: `Phone ${key} incidents` });
      await capture({ file: `phone-${key}-incident.png`, route: `/dashboard/incidents/detail?id=${scenarios[1].incident.id}`, label: `Phone ${key} incident detail` });
      await capture({ file: `phone-${key}-approvals.png`, route: "/dashboard/approvals", label: `Phone ${key} approvals` });
      await capture({ file: `phone-${key}-session.png`, route: `/dashboard/sessions/detail?id=${scenarios[1].session.id}`, label: `Phone ${key} session`, requireEvents: true });
    } finally {
      page = adminPage;
      await phoneContext.close();
    }
  }
}

let token = null;
let originalTier = null;
let runError = null;

try {
  token = await login();
  const scenarios = await loadScenarioState(token);
  originalTier = (await requestJson("GET", "/config", token)).tier;

  await capture({ file: "incidents-list.png", route: "/dashboard/incidents", label: "Incident command center" });
  await capture({ file: "incidents-admin.png", route: "/dashboard/incidents", label: "Admin incident board" });
  await capture({ file: "incident-detail.png", route: `/dashboard/incidents/detail?id=${scenarios[1].incident.id}`, label: "Incident detail" });

  const adminPage = page;
  const operatorContext = await browser.newContext({ viewport: VIEWPORT, colorScheme: "dark", deviceScaleFactor: SCALE });
  try {
    page = await operatorContext.newPage();
    monitor(page);
    const operatorToken = await tokenFor("john", OPERATOR_PASSWORD);
    await page.addInitScript((accessToken) => {
      localStorage.setItem("opsmender_token", accessToken);
      localStorage.setItem("opsmender:theme", "dark");
    }, operatorToken);
    await capture({ file: "incidents-operator.png", route: "/dashboard/incidents", label: "Operator incident board" });
  } finally {
    page = adminPage;
    await operatorContext.close();
  }

  await setWorkspaceTier(token, 1);
  await capture({ file: "live-session-detail.png", route: `/dashboard/sessions/detail?id=${scenarios[1].session.id}`, label: "Tier 1 live session detail", requireEvents: true });
  await capture({ file: "approvals-pending.png", route: "/dashboard/approvals", label: "Pending approval inbox" });
  await capture({ file: "tier-1.png", route: "/dashboard/approvals", label: "Tier 1 paused for approval" });
  await capturePhones(token, scenarios);

  await setWorkspaceTier(token, 0);
  await capture({ file: "tier-0.png", route: `/dashboard/sessions/detail?id=${scenarios[0].session.id}`, label: "Tier 0 autonomous remediation", requireEvents: true, focusEvents: true });
  await capture({ file: "ai-session.png", route: `/dashboard/sessions/detail?id=${scenarios[0].session.id}`, label: "AI session", requireEvents: true, focusEvents: true });

  await setWorkspaceTier(token, 2);
  await capture({ file: "tier-2.png", route: `/dashboard/sessions/detail?id=${scenarios[2].session.id}`, label: "Tier 2 advisory analysis", requireEvents: true, focusEvents: true });
  await capture({ file: "settings.png", route: "/dashboard/config", label: "Workspace settings" });
  await capture({ file: "mcp-skills.png", route: "/dashboard/skills", label: "Skill Studio" });
  await capture({ file: "people-rbac.png", route: "/dashboard/people", label: "People and access" });
} catch (error) {
  runError = error;
} finally {
  if (token !== null && originalTier !== null) {
    try { await setWorkspaceTier(token, originalTier); } catch (error) { runError ??= error; }
  }
  await browser.close();
}

if (runError) throw runError;
console.log(`Done. Twenty one verified screenshots saved to ${path.relative(process.cwd(), OUT)}.`);
