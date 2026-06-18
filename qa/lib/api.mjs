// Thin REST helpers for the QA walkthrough.
//
// The walkthrough drives the real UI for every feature; the API is used only
// to (a) discover the org id after login and (b) optionally clean up the
// QA-prefixed entities afterwards. Requests go through a Playwright
// APIRequestContext so they share the browser's cookie/proxy behaviour.

import { config } from "./config.mjs";

// Log in over the API purely to read back the primary org id (the UI login
// is exercised separately as a real feature test).
export async function discoverOrgId(request, token) {
  const res = await request.get(`${config.baseUrl}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok()) {
    throw new Error(`GET /auth/me failed: ${res.status()} ${await res.text()}`);
  }
  const me = await res.json();
  return me.primary_org_id ?? me.org_id ?? null;
}

export async function apiLogin(request) {
  const res = await request.post(`${config.baseUrl}/auth/login`, {
    data: { username: config.username, password: config.password },
  });
  if (!res.ok()) {
    throw new Error(`POST /auth/login failed: ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  const token = body.access_token ?? body.token;
  const orgId = await discoverOrgId(request, token);
  return { token, orgId };
}

function authHeaders(auth) {
  return {
    Authorization: `Bearer ${auth.token}`,
    ...(auth.orgId ? { "X-Org-ID": auth.orgId } : {}),
  };
}

// List items from a paginated/list endpoint, tolerating both `{items:[...]}`
// and bare-array shapes.
async function listAll(request, auth, route) {
  const res = await request.get(`${config.baseUrl}${route}`, {
    headers: authHeaders(auth),
  });
  if (!res.ok()) return [];
  const body = await res.json().catch(() => null);
  if (Array.isArray(body)) return body;
  if (body && Array.isArray(body.items)) return body.items;
  return [];
}

async function del(request, auth, route) {
  const res = await request.delete(`${config.baseUrl}${route}`, {
    headers: authHeaders(auth),
  });
  return res.ok() || res.status() === 404;
}

// Best-effort cleanup: delete every entity whose name starts with the run's
// QA prefix. Ordered so foreign-key dependents go before their parents.
// Returns a list of human-readable result lines.
export async function cleanup(request, auth) {
  const prefix = config.runId;
  const lines = [];
  const sweep = async (label, listRoute, delRoute, nameField = "name") => {
    let items = [];
    try {
      items = await listAll(request, auth, listRoute);
    } catch {
      lines.push(`  - ${label}: list failed (skipped)`);
      return;
    }
    const mine = items.filter((it) =>
      String(it[nameField] ?? it.title ?? "").startsWith(prefix),
    );
    let ok = 0;
    for (const it of mine) {
      if (await del(request, auth, delRoute(it.id))) ok += 1;
    }
    if (mine.length) lines.push(`  - ${label}: deleted ${ok}/${mine.length}`);
  };

  // Dependents first.
  await sweep("incidents", "/incidents?limit=200", (id) => `/incidents/${id}`, "title");
  await sweep("services", "/services", (id) => `/services/${id}`);
  await sweep("escalation-chains", "/escalation-chains", (id) => `/escalation-chains/${id}`);
  await sweep("rosters", "/rosters", (id) => `/rosters/${id}`);
  await sweep("sla-targets", "/sla-targets", (id) => `/sla-targets/${id}`);
  await sweep("notification-channels", "/bot-connectors", (id) => `/bot-connectors/${id}`);
  await sweep("models", "/models", (id) => `/models/${id}`);
  // Teams last — services/rosters/chains reference them.
  await sweep("teams", "/teams", (id) => `/teams/${id}`);

  return lines.length ? lines : ["  - nothing to clean up"];
}
