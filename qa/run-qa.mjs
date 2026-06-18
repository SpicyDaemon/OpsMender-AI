#!/usr/bin/env node
// OpsMender manual-QA walkthrough.
//
// Drives the real UI end-to-end — login, set up a team / service / escalation
// policy / roster, check the roster calendar, exercise notifications, create
// and resolve an incident, create an SLA target, test a model connection —
// then logs out. Console errors, unhandled page errors, and 5xx responses are
// captured per step. A JSON + Markdown report is written to qa/report/.
//
// Usage:
//   node qa/run-qa.mjs
//   QA_BASE_URL=http://localhost:8000 QA_USERNAME=admin QA_PASSWORD=… node qa/run-qa.mjs
//   QA_HEADLESS=false QA_FEATURES=teams,incidents node qa/run-qa.mjs
//
// See qa/README.md for every parameter.

import { Harness } from "./lib/harness.mjs";
import { cleanup } from "./lib/api.mjs";
import { config } from "./lib/config.mjs";
import { features } from "./features/index.mjs";

const h = new Harness();

console.log(`OpsMender QA walkthrough → ${config.baseUrl}`);
console.log(`Run id: ${config.runId}  (entities created this run carry this prefix)`);
if (config.only.length) console.log(`Features: ${config.only.join(", ")}`);

let exitCode = 1;
try {
  await h.start();

  for (const feature of features) {
    if (!h.shouldRun(feature.id)) continue;
    await h.feature(feature.id, feature.title, feature.run);

    // Once authenticated, persist the captured session for cleanup.
    if (feature.id === "auth" && !h.auth) {
      console.log("   (authentication failed — aborting remaining features)");
      break;
    }
  }

  // Optional best-effort cleanup of the entities this run created.
  if (config.cleanup && h.auth) {
    console.log("\n── Cleanup ──");
    const lines = await cleanup(h.request, h.auth);
    for (const l of lines) console.log(l);
  }

  exitCode = await h.finish();
} catch (err) {
  console.error("\nFatal error running QA walkthrough:", err);
  try {
    exitCode = await h.finish();
  } catch {
    /* ignore */
  }
  exitCode = 1;
}

process.exit(exitCode);
