// Playwright screenshot capture for the OpsMender dashboard.
// Run: node scripts/take_screenshots.mjs
//
// Assumes the dev server is running on http://localhost:8000 with the
// seeded demo DB. Captures every operator-facing route + a handful of
// key modals into ./screenshots/.

import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

const BASE = process.env.OPSMENDER_BASE_URL || "http://localhost:8000";
const OUT = path.resolve("screenshots");
const ADMIN = { username: "admin", password: "admin123" };
const VIEWPORT = { width: 1440, height: 900 };

await fs.mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: VIEWPORT });
const page = await ctx.newPage();

// Quiet down image 404s etc. in the console.
page.on("pageerror", (e) => console.error("pageerror:", e.message));

async function shot(name) {
  const f = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: f, fullPage: true });
  console.log("✓", path.relative(process.cwd(), f));
}

async function goto(url, opts = {}) {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle", timeout: 20000 });
  // Tiny settle for any post-mount fetches that fire after networkidle.
  await page.waitForTimeout(opts.wait ?? 600);
}

// ---------- Public routes ----------

await goto("/login");
await shot("00_login");

await goto("/register");
await shot("01_register");

// ---------- Authenticate via API + token injection ----------

const tokenResp = await page.request.post(`${BASE}/auth/login`, {
  data: ADMIN,
});
if (!tokenResp.ok()) {
  throw new Error(`Login failed: ${tokenResp.status()} ${await tokenResp.text()}`);
}
const { access_token } = await tokenResp.json();

const me = await (
  await page.request.get(`${BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${access_token}` },
  })
).json();
const orgId = me.primary_org_id;

await page.addInitScript(
  ({ t, o }) => {
    localStorage.setItem("opsmender_token", t);
    localStorage.setItem("opsmender_org_id", o);
  },
  { t: access_token, o: orgId },
);

// Reload to pick up the seeded auth.
await goto("/dashboard/incidents");

// ---------- Incident Management ----------

await shot("02_incidents_list");

// Open create-incident modal
await page.getByRole("button", { name: /new incident/i }).click().catch(() => {});
await page.waitForTimeout(500);
await shot("03_incidents_new_modal");
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// Open fire-test-incident modal
await page.getByRole("button", { name: /fire test incident/i }).click().catch(() => {});
await page.waitForTimeout(500);
await shot("04_incidents_fire_test_modal");
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// Click first incident row → detail
const firstIncidentLink = page.locator(
  'a[href^="/dashboard/incidents/detail?id="]',
).first();
const incidentHref = await firstIncidentLink.getAttribute("href").catch(() => null);
if (incidentHref) {
  await goto(incidentHref);
  await shot("05_incident_detail");
}

await goto("/dashboard/approvals");
await shot("06_approvals");

// Try to grab an active-session detail page from the API
const sessionsResp = await page.request.get(`${BASE}/sessions?limit=5`, {
  headers: {
    Authorization: `Bearer ${access_token}`,
    "X-Org-ID": orgId,
  },
});
if (sessionsResp.ok()) {
  const body = await sessionsResp.json();
  const first = body.items?.[0];
  if (first) {
    await goto(`/dashboard/sessions/detail?id=${first.id}`);
    await shot("07_session_detail");
  }
}

// ---------- Paging & On-call ----------

const pagingRoutes = [
  ["teams", "10_paging_teams"],
  ["services", "11_paging_services"],
  ["rosters", "12_paging_rosters"],
  ["priority-rules", "13_paging_priority_rules"],
  ["escalation-chains", "14_paging_escalation_chains"],
  ["maintenance-windows", "15_paging_maintenance_windows"],
  ["my-notifications", "16_paging_my_notifications"],
];
for (const [slug, name] of pagingRoutes) {
  await goto(`/dashboard/paging/${slug}`);
  await shot(name);
}

// ---------- AI Agent ----------

const aiAgent = [
  ["skills", "20_ai_skills"],
  ["memories", "21_ai_memories"],
  ["mcp-servers", "22_ai_mcp_servers"],
  ["models", "23_ai_models"],
  ["workflows", "24_ai_workflows"],
  ["agent-teams", "25_ai_agent_teams"],
];
for (const [slug, name] of aiAgent) {
  await goto(`/dashboard/${slug}`);
  await shot(name);
}

// ---------- Integrations ----------

for (const [slug, name] of [
  ["bot-connectors", "30_integ_bot_connectors"],
  ["webhooks", "31_integ_webhooks"],
  ["ingest-tokens", "32_integ_ingest_tokens"],
]) {
  await goto(`/dashboard/${slug}`);
  await shot(name);
}

// ---------- Observe ----------

for (const [slug, name] of [
  ["scans", "40_observe_scans"],
  ["reliability", "41_observe_reliability"],
  ["activity", "42_observe_activity"],
]) {
  await goto(`/dashboard/${slug}`);
  await shot(name);
}

// ---------- Admin ----------

// People — Users tab
await goto("/dashboard/people");
await shot("50_admin_people_users");

// People — Invites tab
const invitesTab = page.getByRole("button", { name: /^invites/i });
await invitesTab.click().catch(() => {});
await page.waitForTimeout(500);
await shot("51_admin_people_invites");

// New-invite modal
const newInviteBtn = page.getByRole("button", { name: /new invite/i });
await newInviteBtn.click().catch(() => {});
await page.waitForTimeout(500);
await shot("52_admin_people_new_invite_modal");
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// People detail — fetch the first non-admin user
const usersResp = await page.request.get(`${BASE}/auth/users?limit=10`, {
  headers: {
    Authorization: `Bearer ${access_token}`,
    "X-Org-ID": orgId,
  },
});
if (usersResp.ok()) {
  const ul = await usersResp.json();
  const target = ul.items.find((u) => u.username !== "admin") ?? ul.items[0];
  await goto(`/dashboard/people/detail?id=${target.id}`);
  await shot("53_admin_people_detail");
}

// Organizations
await goto("/dashboard/organizations");
await shot("54_admin_organizations");

// Config
await goto("/dashboard/config");
await shot("55_admin_config");

// ---------- Public invite-accept (use the seeded pending invite) ----------
// We seeded a pending invite with token_hash = sha256("invite-pending");
// the raw token isn't recoverable so we just show the invalid-token state
// for documentation purposes.
await goto("/invite?token=demo-invalid-token");
await shot("60_public_invite_invalid");

await browser.close();
console.log("\nDone. Screenshots saved to ./screenshots/");
