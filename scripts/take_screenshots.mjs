// Curated README screenshot capture for OpsMender.
//
// Run: node scripts/take_screenshots.mjs
//
// Env:
//   OPSMENDER_BASE_URL  default http://localhost:8000
//   OPSMENDER_EMAIL     login override
//   OPSMENDER_PASSWORD  login override
//
// If the login overrides are absent, the script reads
// OPSMENDER_BOOTSTRAP_ADMIN_EMAIL / OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD from
// the process environment or the local .env file. It writes the four launch
// screenshots used by README.md into site/public/screenshots/.

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
const RUNNING_SESSION_STATUSES = new Set(["queued", "active", "awaiting_approval"]);
const SCREENSHOTS = [
  {
    file: "incidents-list.png",
    route: "/dashboard/incidents",
    label: "Incidents list",
  },
  {
    file: "live-session-detail.png",
    route: ({ sessionId }) => `/dashboard/sessions/detail?id=${sessionId}`,
    label: "Live session detail",
  },
  {
    file: "approvals-pending.png",
    route: "/dashboard/approvals",
    label: "Pending approvals",
  },
  {
    file: "settings.png",
    route: "/dashboard/config",
    label: "Settings",
  },
];

if (!EMAIL || !PASSWORD) {
  throw new Error(
    "Set OPSMENDER_EMAIL/OPSMENDER_PASSWORD or OPSMENDER_BOOTSTRAP_ADMIN_* in the environment or .env.",
  );
}

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: VIEWPORT,
  colorScheme: "dark",
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();

page.on("pageerror", (e) => console.error("pageerror:", e.message));

async function settle(ms = 900) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function requestJson(method, url, token, data) {
  const resp = await page.request.fetch(`${BASE}${url}`, {
    method,
    data,
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok()) {
    throw new Error(`${method} ${url} failed: ${resp.status()} ${await resp.text()}`);
  }
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

async function listPendingApprovals(token) {
  const res = await requestJson("GET", "/approvals?status=pending&limit=20", token);
  return res?.items || [];
}

async function listSessions(token) {
  const res = await requestJson("GET", "/sessions?limit=20", token);
  return res?.items || [];
}

function pickSession(sessions, pendingApprovals) {
  const approvalSessionId = pendingApprovals[0]?.session_id;
  return (
    sessions.find((session) => session.id === approvalSessionId) ||
    sessions.find((session) => RUNNING_SESSION_STATUSES.has(session.status)) ||
    sessions[0] ||
    null
  );
}

async function ensureUsefulDemoState(token) {
  let pendingApprovals = await listPendingApprovals(token);
  let sessions = await listSessions(token);

  if (pendingApprovals.length === 0 || sessions.length === 0) {
    console.log("No pending approval/session found; creating a synthetic walkthrough record.");
    const fired = await requestJson("POST", "/incidents/fire-test", token, {});
    const incidentId = fired?.incident?.id;
    if (incidentId) {
      await requestJson("POST", `/incidents/${incidentId}/ack`, token, {
        via: "api",
      }).catch((err) => {
        console.warn(String(err));
      });
      await requestJson("POST", "/sessions", token, {
        incident_id: incidentId,
        tier: 1,
        initial_briefing:
          "Prepare a safe remediation plan for the synthetic launch screenshot incident.",
        force: true,
      }).catch((err) => {
        console.warn(String(err));
      });
      await page.waitForTimeout(3000);
    }
    pendingApprovals = await listPendingApprovals(token);
    sessions = await listSessions(token);
  }

  const session = pickSession(sessions, pendingApprovals);
  if (!session) {
    throw new Error("No session was available for the live session screenshot.");
  }
  if (pendingApprovals.length === 0) {
    throw new Error(
      "No pending approval was found after the fallback walkthrough. Re-run the cleaned demo seed before capturing README screenshots.",
    );
  }

  return {
    sessionId: session.id,
    pendingApprovalCount: pendingApprovals.length,
  };
}

async function login() {
  const tokenResp = await page.request.post(`${BASE}/auth/login`, {
    data: { username: EMAIL, password: PASSWORD },
  });
  if (!tokenResp.ok()) {
    throw new Error(`Login failed: ${tokenResp.status()} ${await tokenResp.text()}`);
  }
  const { access_token: token } = await tokenResp.json();
  await page.addInitScript((accessToken) => {
    localStorage.setItem("opsmender_token", accessToken);
    localStorage.setItem("opsmender:theme", "dark");
  }, token);
  return token;
}

async function capture(file, route) {
  const target = `${BASE}${route}`;
  await page.goto(target, { waitUntil: "domcontentloaded", timeout: 30000 });
  await settle();
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.getElementById("main-content")?.scrollTo(0, 0);
  });
  await page.waitForTimeout(150);
  const out = path.join(OUT, file);
  await page.screenshot({ path: out });
  console.log(`✓ ${path.relative(process.cwd(), out)}`);
}

try {
  const token = await login();
  const state = await ensureUsefulDemoState(token);

  for (const shot of SCREENSHOTS) {
    const route =
      typeof shot.route === "function" ? shot.route(state) : shot.route;
    console.log(`${shot.label}: ${route}`);
    await capture(shot.file, route);
  }

  console.log(
    `\nDone. Screenshots saved to ${path.relative(process.cwd(), OUT)}. Pending approvals: ${state.pendingApprovalCount}.`,
  );
} finally {
  await browser.close();
}
