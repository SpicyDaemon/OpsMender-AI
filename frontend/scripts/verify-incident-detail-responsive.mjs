import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright";

const BASE_URL = process.env.OPSMENDER_BASE_URL ?? "http://localhost:8000";
const USERNAME = process.env.OPSMENDER_USERNAME ?? "admin";
const PASSWORD = process.env.OPSMENDER_PASSWORD ?? "admin123";
const OUTPUT_DIR = path.resolve(
  process.cwd(),
  process.env.OPSMENDER_RESPONSIVE_OUTPUT_DIR ?? "artifacts/incident-detail-responsive",
);

const VIEWPORTS = [
  { name: "phone-320", width: 320, height: 900 },
  { name: "phone-375", width: 375, height: 900 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "desktop-1440", width: 1440, height: 1200 },
];

const EXPECTED_ACTIONS = [
  "action-acknowledge",
  "action-take",
  "action-start-session",
  "action-resolve",
];

async function api(pathname, init = {}, token) {
  const headers = {
    "Content-Type": "application/json",
    ...(init.headers ?? {}),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${BASE_URL}${pathname}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${pathname} -> HTTP ${response.status}: ${body}`);
  }
  return response.json();
}

async function login() {
  const body = await api(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
    },
  );
  return body.access_token;
}

async function seedIncident(token) {
  return api(
    "/incidents",
    {
      method: "POST",
      body: JSON.stringify({
        title:
          "Primary API latency spike causing cascading retries across edge workers and checkout background jobs",
        description:
          "Synthetic incident for responsive verification. This title is intentionally long enough to stress the command strip and the detail header at narrow widths.",
        severity: "high",
      }),
    },
    token,
  );
}

async function verifyViewport(page, incidentId, viewport) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.goto(
    `${BASE_URL}/dashboard/incidents/detail?id=${incidentId}`,
    { waitUntil: "networkidle" },
  );
  await page.screenshot({
    path: path.join(OUTPUT_DIR, `${viewport.name}.png`),
    fullPage: true,
  });

  const metrics = await page.evaluate(() => {
    const strip = document.querySelector('[data-testid="incident-command-strip"]');
    const actions = Array.from(
      document.querySelectorAll('[data-testid^="action-"]'),
    ).map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        id: node.getAttribute("data-testid"),
        text: node.textContent?.replace(/\s+/g, " ").trim() ?? "",
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
      };
    });

    const stripRect = strip?.getBoundingClientRect();
    return {
      title: document.title,
      innerWidth: window.innerWidth,
      docClientWidth: document.documentElement.clientWidth,
      docScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      hasHorizontalOverflow:
        document.documentElement.scrollWidth > window.innerWidth,
      stripHeight: stripRect ? Math.round(stripRect.height) : null,
      hasStrip: Boolean(strip),
      actions,
    };
  });

  const failures = [];
  if (!metrics.hasStrip) {
    failures.push("command strip did not render");
  }
  if (metrics.hasHorizontalOverflow) {
    failures.push(
      `horizontal overflow detected (${metrics.docScrollWidth}px > ${metrics.innerWidth}px)`,
    );
  }

  const renderedActionIds = new Set(metrics.actions.map((item) => item.id));
  for (const actionId of EXPECTED_ACTIONS) {
    if (!renderedActionIds.has(actionId)) {
      failures.push(`missing expected action ${actionId}`);
    }
  }

  return { viewport, metrics, failures };
}

async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });

  const token = await login();
  const me = await api("/auth/me", {}, token);
  const incident = await seedIncident(token);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.evaluate(
    ({ tokenValue, orgId }) => {
      localStorage.setItem("opsmender_token", tokenValue);
      localStorage.setItem("opsmender_org_id", orgId);
    },
    { tokenValue: token, orgId: me.primary_org_id },
  );

  const results = [];
  for (const viewport of VIEWPORTS) {
    results.push(await verifyViewport(page, incident.id, viewport));
  }

  await browser.close();
  await fs.writeFile(
    path.join(OUTPUT_DIR, "metrics.json"),
    `${JSON.stringify(results, null, 2)}\n`,
    "utf8",
  );

  const failures = results.flatMap((result) =>
    result.failures.map((failure) => `${result.viewport.name}: ${failure}`),
  );

  console.log(JSON.stringify(results, null, 2));

  if (failures.length > 0) {
    throw new Error(`Responsive verification failed:\n${failures.join("\n")}`);
  }

  console.log(
    `Responsive verification passed. Artifacts written to ${OUTPUT_DIR}`,
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
