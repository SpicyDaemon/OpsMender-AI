// Curated README screenshot capture for OpsMender.
//
// Run against a fresh database created by scripts/seed_demo.py:
//   node scripts/take_screenshots.mjs
//
// Env:
//   OPSMENDER_BASE_URL       default http://localhost:8000
//   OPSMENDER_EMAIL          login override
//   OPSMENDER_PASSWORD       login override
//   OPSMENDER_SCREENSHOT_DIR default site/public/screenshots

// The script never starts an AI session. It captures only deterministic seeded
// scenarios and restores the workspace tier even when a capture fails.

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

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
const OUT = path.resolve(
  process.env.OPSMENDER_SCREENSHOT_DIR || "site/public/screenshots",
);

const VIEWPORT = { width: 1440, height: 900 };
const SCENARIO_IDS = ["demo-tier-0", "demo-tier-1", "demo-tier-2"];
const FORBIDDEN_TEXT = [
  "invalid x-api-key",
  "no events were recorded",
  "session failed",
  "failed to load session",
];

if (!EMAIL || !PASSWORD) {
  throw new Error(
    "Set OPSMENDER_EMAIL/OPSMENDER_PASSWORD or OPSMENDER_BOOTSTRAP_ADMIN_* in the environment or .env.",
  );
}

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: VIEWPORT,
  colorScheme: "dark",
  deviceScaleFactor: 1,
});
const page = await context.newPage();

let responseFailures = [];
let pageErrors = [];
page.on("response", (response) => {
  const status = response.status();
  if (status === 401 || status === 403 || status >= 500) {
    responseFailures.push(`${status} ${response.request().method()} ${response.url()}`);
  }
});
page.on("pageerror", (error) => pageErrors.push(error.message));

async function settle(ms = 900) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function requestJson(method, url, token, data) {
  const options = {
    method,
    headers: { Authorization: `Bearer ${token}` },
  };
  if (data !== undefined) options.data = data;
  const response = await page.request.fetch(`${BASE}${url}`, options);
  if (!response.ok()) {
    throw new Error(
      `${method} ${url} failed: ${response.status()} ${await response.text()}`,
    );
  }
  const body = await response.text();
  return body ? JSON.parse(body) : null;
}

async function login() {
  const response = await page.request.post(`${BASE}/auth/login`, {
    data: { username: EMAIL, password: PASSWORD },
  });
  if (!response.ok()) {
    throw new Error(`Login failed: ${response.status()} ${await response.text()}`);
  }
  const { access_token: token } = await response.json();
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
  const incidents = new Map(
    incidentResponse.items
      .filter((incident) => SCENARIO_IDS.includes(incident.external_id))
      .map((incident) => [incident.external_id, incident]),
  );
  const sessionsByIncident = new Map(
    sessionResponse.items.map((session) => [session.incident_id, session]),
  );
  const scenarios = {};

  for (let tier = 0; tier <= 2; tier += 1) {
    const externalId = `demo-tier-${tier}`;
    const incident = incidents.get(externalId);
    const session = incident ? sessionsByIncident.get(incident.id) : null;
    if (!incident || !session || session.tier !== tier) {
      throw new Error(
        `Fresh demo seed is required: missing deterministic Tier ${tier} incident/session.`,
      );
    }
    scenarios[tier] = { incident, session };
  }

  const tier1Approval = approvalResponse.items.find(
    (approval) => approval.session_id === scenarios[1].session.id,
  );
  if (!tier1Approval) {
    throw new Error("Fresh demo seed is required: Tier 1 pending approval is missing.");
  }

  return scenarios;
}

async function setWorkspaceTier(token, tier) {
  const config = await requestJson("PUT", "/config", token, { tier });
  if (config.tier !== tier) {
    throw new Error(`Workspace tier remained ${config.tier}; expected ${tier}.`);
  }
}

async function assertCleanCapture(label, requireEvents) {
  const bodyText = (await page.locator("body").innerText()).toLowerCase();
  const forbidden = FORBIDDEN_TEXT.find((text) => bodyText.includes(text));
  if (forbidden) {
    throw new Error(`${label} contains forbidden state: ${forbidden}`);
  }
  const toasts = await page.locator(".ops-toast").allInnerTexts();
  if (toasts.length > 0) {
    throw new Error(`${label} rendered a toast: ${toasts.join(" | ")}`);
  }
  if (responseFailures.length > 0) {
    throw new Error(`${label} made forbidden requests:\n${responseFailures.join("\n")}`);
  }
  if (pageErrors.length > 0) {
    throw new Error(`${label} raised page errors:\n${pageErrors.join("\n")}`);
  }
  if (requireEvents) {
    const eventLog = page.locator('[role="log"][aria-label="Session event stream"]');
    await eventLog.waitFor({ state: "visible", timeout: 15000 });
    const eventText = (await eventLog.innerText()).trim();
    if (!eventText || /waiting for events/i.test(eventText)) {
      throw new Error(`${label} has an empty session event stream.`);
    }
  }
}

async function capture({
  file,
  route,
  label,
  requireEvents = false,
  focusEvents = false,
}) {
  responseFailures = [];
  pageErrors = [];
  await page.goto(`${BASE}${route}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await settle();
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.getElementById("main-content")?.scrollTo(0, 0);
  });
  if (focusEvents) {
    const eventLog = page.locator('[role="log"][aria-label="Session event stream"]');
    await eventLog.waitFor({ state: "visible", timeout: 15000 });
    await eventLog.evaluate((element) => {
      const main = element.closest("main");
      const panel = element.parentElement;
      if (!main || !panel) return;
      const offset =
        panel.getBoundingClientRect().top -
        main.getBoundingClientRect().top +
        main.scrollTop;
      main.scrollTo({ top: Math.max(0, offset - 170), behavior: "instant" });
    });
  }
  await page.waitForTimeout(150);
  await assertCleanCapture(label, requireEvents);
  const output = path.join(OUT, file);
  await page.screenshot({ path: output, animations: "disabled" });
  console.log(`OK ${label}: ${path.relative(process.cwd(), output)}`);
}

let token = null;
let originalTier = null;
let runError = null;

try {
  token = await login();
  const scenarios = await loadScenarioState(token);
  originalTier = (await requestJson("GET", "/config", token)).tier;

  await capture({
    file: "incidents-list.png",
    route: "/dashboard/incidents",
    label: "Incident command center",
  });

  await setWorkspaceTier(token, 1);
  await capture({
    file: "live-session-detail.png",
    route: `/dashboard/sessions/detail?id=${scenarios[1].session.id}`,
    label: "Tier 1 live session detail",
    requireEvents: true,
  });
  await capture({
    file: "approvals-pending.png",
    route: "/dashboard/approvals",
    label: "Pending approval inbox",
  });
  await capture({
    file: "tier-1.png",
    route: "/dashboard/approvals",
    label: "Tier 1 paused for approval",
  });

  await setWorkspaceTier(token, 0);
  await capture({
    file: "tier-0.png",
    route: `/dashboard/sessions/detail?id=${scenarios[0].session.id}`,
    label: "Tier 0 autonomous remediation",
    requireEvents: true,
    focusEvents: true,
  });

  await setWorkspaceTier(token, 2);
  await capture({
    file: "tier-2.png",
    route: `/dashboard/sessions/detail?id=${scenarios[2].session.id}`,
    label: "Tier 2 advisory analysis",
    requireEvents: true,
    focusEvents: true,
  });
  await capture({
    file: "settings.png",
    route: "/dashboard/config",
    label: "Workspace settings",
  });
} catch (error) {
  runError = error;
} finally {
  if (token !== null && originalTier !== null) {
    try {
      await setWorkspaceTier(token, originalTier);
      console.log(`Restored workspace tier to ${originalTier}.`);
    } catch (error) {
      runError ??= error;
      console.error(`Could not restore workspace tier: ${error}`);
    }
  }
  await browser.close();
}

if (runError) throw runError;
console.log(`Done. Seven verified screenshots saved to ${path.relative(process.cwd(), OUT)}.`);
