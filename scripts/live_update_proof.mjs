// A-1 live-update acceptance proof for OpsMender.
//
// Run: node scripts/live_update_proof.mjs
//
// Opens the dashboard in one Playwright browser context, creates a critical
// incident through the API from a second context, and asserts the dashboard's
// "Critical, open" attention card updates within two seconds through the
// shared notification WebSocket.
//
// Env:
//   OPSMENDER_BASE_URL  default http://localhost:8000
//   OPSMENDER_EMAIL     login override
//   OPSMENDER_PASSWORD  login override
//
// If login overrides are absent, the script reads
// OPSMENDER_BOOTSTRAP_ADMIN_EMAIL / OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD from
// the process environment or the local .env file.

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
const LIVE_UPDATE_TIMEOUT_MS = 2_000;

if (!EMAIL || !PASSWORD) {
  throw new Error(
    "Set OPSMENDER_EMAIL/OPSMENDER_PASSWORD or OPSMENDER_BOOTSTRAP_ADMIN_* in the environment or .env.",
  );
}

function criticalOpenSnapshot() {
  const label = Array.from(document.querySelectorAll("p")).find(
    (el) => el.textContent?.trim() === "Critical, open",
  );
  const card = label?.closest("div.flex.flex-col.rounded-xl");
  const badge = card?.querySelector("span.inline-flex");
  const raw = badge?.textContent?.trim() || "";
  const count = Number.parseInt(raw, 10);
  return {
    count: Number.isFinite(count) ? count : null,
    loading:
      raw === "..." ||
      raw === "\u2026" ||
      Boolean(card?.textContent?.includes("Scanning incidents")),
    text: card?.textContent || "",
  };
}

async function login(page) {
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

async function requestJson(page, method, url, token, data) {
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

async function settle(page, ms = 700) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function waitForCriticalCount(page) {
  await page.waitForFunction(
    () => {
      const snapshot = window.__opsmenderCriticalOpenSnapshot();
      return snapshot.count !== null && !snapshot.loading;
    },
    null,
    { polling: 100, timeout: 15_000 },
  );
  return await page.evaluate(criticalOpenSnapshot);
}

const browser = await chromium.launch({ headless: true });
const dashboardContext = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
});
const apiContext = await browser.newContext();
const page = await dashboardContext.newPage();
const apiPage = await apiContext.newPage();

const activeNotificationSockets = new Set();
let openedNotificationSockets = 0;
let skeletonFlash = false;
let incidentId = null;
let monitor = null;

try {
  await login(page);
  const apiToken = await login(apiPage);

  page.on("websocket", (ws) => {
    if (!ws.url().includes("/notifications/stream")) return;
    openedNotificationSockets += 1;
    activeNotificationSockets.add(ws);
    ws.on("close", () => activeNotificationSockets.delete(ws));
  });

  await page.addInitScript({
    content: `window.__opsmenderCriticalOpenSnapshot = ${criticalOpenSnapshot.toString()};`,
  });
  await page.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded" });
  await settle(page);

  const before = await waitForCriticalCount(page);
  if (before.count === null) {
    throw new Error(`Critical, open count did not settle: ${before.text}`);
  }

  await page.waitForFunction(
    () => performance.getEntriesByType("resource").some((entry) =>
      entry.name.includes("/notifications/stream"),
    ),
    null,
    { timeout: 5_000 },
  ).catch(() => {});

  if (openedNotificationSockets !== 1 || activeNotificationSockets.size !== 1) {
    throw new Error(
      `Expected one notification stream, opened=${openedNotificationSockets}, active=${activeNotificationSockets.size}`,
    );
  }

  monitor = setInterval(async () => {
    try {
      const snapshot = await page.evaluate(criticalOpenSnapshot);
      if (snapshot.loading) skeletonFlash = true;
    } catch {
      // The assertion below owns failures; this best-effort sampler only
      // records whether the card returned to its skeleton state.
    }
  }, 75);

  const services = await requestJson(apiPage, "GET", "/services", apiToken);
  const service = services?.items?.find((item) => item.is_active !== false) || services?.items?.[0];
  if (!service) {
    throw new Error("No service is available for the manual critical incident proof.");
  }

  const proofId = `a1-live-proof-${Date.now()}`;
  const started = Date.now();
  const incident = await requestJson(apiPage, "POST", "/incidents", apiToken, {
    title: `A-1 live update proof ${new Date().toISOString()}`,
    description:
      "Synthetic acceptance-check incident proving the dashboard updates from the notification WebSocket.",
    severity: "critical",
    service_id: service.id,
    external_id: proofId,
    external_source: "manual",
  });
  incidentId = incident?.id;

  await page.waitForFunction(
    ({ expected }) => {
      const snapshot = window.__opsmenderCriticalOpenSnapshot();
      return snapshot.count !== null && snapshot.count >= expected;
    },
    { expected: before.count + 1 },
    { polling: 75, timeout: LIVE_UPDATE_TIMEOUT_MS },
  );
  clearInterval(monitor);
  monitor = null;

  const after = await page.evaluate(criticalOpenSnapshot);
  const elapsed = Date.now() - started;

  if (after.count === null || after.count < before.count + 1) {
    throw new Error(`Critical, open did not increment: ${before.count} -> ${after.count}`);
  }
  if (skeletonFlash) {
    throw new Error("Dashboard attention card flashed its skeleton during the live refresh.");
  }
  if (openedNotificationSockets !== 1 || activeNotificationSockets.size !== 1) {
    throw new Error(
      `Expected one notification stream after update, opened=${openedNotificationSockets}, active=${activeNotificationSockets.size}`,
    );
  }

  console.log(
    `A-1 live proof PASS: Critical, open ${before.count} -> ${after.count} in ${elapsed}ms; websockets=${activeNotificationSockets.size}; skeletonFlash=${skeletonFlash}`,
  );

  // Keep the proof repeatable without leaving dashboard rows behind.
  if (incidentId) {
    await requestJson(apiPage, "DELETE", `/incidents/${incidentId}`, apiToken).catch((err) => {
      console.warn(`Cleanup warning: ${err.message}`);
    });
  }
} finally {
  if (monitor) clearInterval(monitor);
  if (incidentId) {
    // Best-effort second cleanup path in case an assertion above failed before
    // the normal cleanup block.
    await login(apiPage)
      .then((token) => requestJson(apiPage, "DELETE", `/incidents/${incidentId}`, token))
      .catch(() => {});
  }
  await browser.close();
}
