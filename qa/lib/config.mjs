// Central configuration for the OpsMender manual-QA Playwright walkthrough.
//
// Every knob is an environment variable with a sane default, so the suite
// runs with zero setup against a local dev server. On your desktop, point it
// at your running instance and supply credentials / model parameters.
//
// You can also drop a `qa/qa.config.json` file (gitignored) with any of the
// keys below (camelCase) to avoid exporting env vars each run. Environment
// variables always win over the file.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const QA_ROOT = path.resolve(__dirname, "..");

function loadFileConfig() {
  const p = path.join(QA_ROOT, "qa.config.json");
  if (!fs.existsSync(p)) return {};
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (err) {
    console.warn(`[qa] Ignoring malformed qa.config.json: ${err.message}`);
    return {};
  }
}

const file = loadFileConfig();

// env > file > default
function str(envKey, fileKey, dflt) {
  if (process.env[envKey] != null && process.env[envKey] !== "") {
    return process.env[envKey];
  }
  if (file[fileKey] != null) return String(file[fileKey]);
  return dflt;
}
function bool(envKey, fileKey, dflt) {
  const raw = str(envKey, fileKey, null);
  if (raw == null) return dflt;
  return /^(1|true|yes|on)$/i.test(raw);
}
function num(envKey, fileKey, dflt) {
  const raw = str(envKey, fileKey, null);
  if (raw == null) return dflt;
  const n = Number(raw);
  return Number.isFinite(n) ? n : dflt;
}

const runId = str("QA_RUN_ID", "runId", `QA-${Date.now()}`);

export const config = {
  // Where the OpsMender app + API are served (same origin in OpsMender).
  baseUrl: str("QA_BASE_URL", "baseUrl", "http://localhost:8000").replace(/\/$/, ""),

  // Admin (or operator) credentials used for the UI login.
  username: str("QA_USERNAME", "username", "admin@localhost"),
  password: str("QA_PASSWORD", "password", "admin123"),

  // Browser behaviour.
  headless: bool("QA_HEADLESS", "headless", true),
  slowMo: num("QA_SLOWMO", "slowMo", 0),
  // Optional: path to a Chromium/Chrome executable, for environments where
  // `npx playwright install` can't reach the CDN. Empty = use Playwright's
  // bundled browser.
  executablePath: str("QA_CHROMIUM_PATH", "chromiumPath", ""),
  defaultTimeout: num("QA_TIMEOUT", "timeout", 20000),
  viewport: { width: 1440, height: 900 },

  // Unique prefix stamped onto every entity this run creates, so they are
  // easy to spot and (optionally) clean up.
  runId,
  // Best-effort deletion of QA-prefixed entities via the REST API at the end.
  // Off by default so nothing is ever deleted without opting in.
  cleanup: bool("QA_CLEANUP", "cleanup", false),

  // Feature toggles for side-effecting checks.
  // Create a model config during the walkthrough (needs provider params below).
  createModel: bool("QA_CREATE_MODEL", "createModel", false),
  // Click "Test" on the first model config (hits the real provider).
  testModelConnection: bool("QA_TEST_MODEL_CONNECTION", "testModelConnection", true),
  // Actually send a live test notification (MAY page real people).
  sendTestNotification: bool("QA_SEND_TEST_NOTIFICATION", "sendTestNotification", false),
  // Use the synthetic Fire Test Incident flow rather than a real incident.
  fireTestIncident: bool("QA_FIRE_TEST_INCIDENT", "fireTestIncident", true),

  // Optional model-config parameters (only used when createModel = true).
  model: {
    provider: str("QA_MODEL_PROVIDER", "modelProvider", "openai"),
    modelId: str("QA_MODEL_ID", "modelId", "gpt-4o-mini"),
    apiKeyEnv: str("QA_MODEL_KEY_ENV", "modelKeyEnv", "OPENAI_API_KEY"),
    baseUrl: str("QA_MODEL_BASE_URL", "modelBaseUrl", ""),
  },

  // Restrict the run to a comma-separated list of feature ids (e.g.
  // "teams,incidents"). Empty = run everything.
  only: str("QA_FEATURES", "features", "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),

  // Report output.
  reportDir: path.resolve(
    QA_ROOT,
    str("QA_REPORT_DIR", "reportDir", "report"),
  ),

  qaRoot: QA_ROOT,
};

// A human-friendly, prefix-stamped name for a created entity.
export function qaName(label) {
  return `${config.runId}-${label}`;
}

// A slug-safe variant of the above (lowercase, hyphens only).
export function qaSlug(label) {
  return `${config.runId}-${label}`.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
