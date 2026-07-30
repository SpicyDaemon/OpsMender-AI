// QA harness: owns the browser/page, captures console + network errors,
// runs features/steps with soft assertions (a failing step is recorded and
// the walkthrough continues), and writes a JSON + Markdown report.

import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { config } from "./config.mjs";

// Console noise that is never actionable for a QA pass.
const CONSOLE_IGNORE = [
  /ResizeObserver loop/i,
  /favicon/i,
  /Download the React DevTools/i,
  /\[Fast Refresh\]/i,
];

export class Harness {
  constructor() {
    this.browser = null;
    this.context = null;
    this.page = null;
    this.features = [];
    this._current = null; // active feature
    this._buffer = []; // {kind, text, url, status} since last snapshot
    // Shared scratch space for data passed between features (created names/ids).
    this.state = {};
    this.auth = null; // {token, orgId}
  }

  async start() {
    this.browser = await chromium.launch({
      headless: config.headless,
      slowMo: config.slowMo,
      ...(config.executablePath ? { executablePath: config.executablePath } : {}),
    });
    this.context = await this.browser.newContext({
      viewport: config.viewport,
      baseURL: config.baseUrl,
      ignoreHTTPSErrors: true,
    });
    this.context.setDefaultTimeout(config.defaultTimeout);
    this.page = await this.context.newPage();

    this.page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (CONSOLE_IGNORE.some((re) => re.test(text))) return;
      this._buffer.push({ kind: "console", text });
    });
    this.page.on("pageerror", (err) => {
      this._buffer.push({ kind: "pageerror", text: err.message });
    });
    this.page.on("response", (res) => {
      const status = res.status();
      if (status >= 500) {
        this._buffer.push({ kind: "http", status, url: res.url() });
      }
    });

    fs.mkdirSync(path.join(config.reportDir, "screenshots"), { recursive: true });
  }

  get request() {
    return this.context.request;
  }

  // ---- structure ---------------------------------------------------------

  shouldRun(featureId) {
    return config.only.length === 0 || config.only.includes(featureId);
  }

  async feature(id, title, fn) {
    if (!this.shouldRun(id)) return;
    const f = { id, title, steps: [] };
    this.features.push(f);
    this._current = f;
    console.log(`\n── ${title} ──`);
    try {
      await fn(this);
    } catch (err) {
      // A feature-level throw (not caught by a step) is recorded as a step.
      this._record("(feature aborted)", "fail", 0, err, []);
      console.log(`   ✗ feature aborted: ${err.message}`);
    }
    this._current = null;
  }

  // Run a single step. Failures are caught and recorded; the walkthrough
  // continues. Throw `Harness.skip(reason)` to mark a step skipped.
  async step(name, fn) {
    const before = this._buffer.length;
    const t0 = Date.now();
    let status = "pass";
    let error = null;
    let shot = null;
    try {
      await fn(this);
    } catch (err) {
      if (err && err.__qaSkip) {
        status = "skip";
        error = err.message;
      } else {
        status = "fail";
        error = err;
        shot = await this._screenshot(name);
      }
    }
    const newEvents = this._buffer.slice(before);
    // Console/page errors during a passing step downgrade it to a warning.
    if (status === "pass" && newEvents.length) status = "warn";
    this._record(name, status, Date.now() - t0, error, newEvents, shot);
    const icon = { pass: "✓", warn: "!", fail: "✗", skip: "–" }[status];
    const extra =
      status === "skip"
        ? ` (skipped: ${error})`
        : error
          ? ` — ${error.message ?? error}`
          : newEvents.length
            ? ` (${newEvents.length} console/network error${newEvents.length > 1 ? "s" : ""})`
            : "";
    console.log(`   ${icon} ${name}${extra}`);
  }

  static skip(reason) {
    const e = new Error(reason);
    e.__qaSkip = true;
    return e;
  }

  _record(name, status, durationMs, error, events, shot) {
    const target = this._current ?? { steps: [] };
    target.steps.push({
      name,
      status,
      durationMs,
      error: error ? (error.stack ?? error.message ?? String(error)) : null,
      events: events.map((e) => ({ ...e })),
      screenshot: shot,
    });
  }

  async _screenshot(name) {
    const safe = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60);
    const file = `${Date.now()}-${safe}.png`;
    const abs = path.join(config.reportDir, "screenshots", file);
    try {
      await this.page.screenshot({ path: abs, fullPage: true });
      return path.join("screenshots", file);
    } catch {
      return null;
    }
  }

  // ---- UI helpers --------------------------------------------------------

  async goto(route) {
    // Dashboard pages keep live-event connections open once a session starts,
    // so networkidle is not a valid readiness signal for the full walkthrough.
    // Feature assertions wait for their own visible content after navigation.
    await this.page.goto(route, { waitUntil: "load" });
  }

  // Click a button by accessible name (string or RegExp).
  async clickButton(name, { timeout } = {}) {
    await this.page
      .getByRole("button", { name, exact: false })
      .first()
      .click({ timeout: timeout ?? config.defaultTimeout });
  }

  // Fill a field. Many OpsMender forms render an unassociated <Label> followed
  // by the control, so we try: associated label → placeholder → the
  // input/textarea that follows a label with the given text → id.
  async fill(value, { label, placeholder, id } = {}) {
    if (label) {
      const byLabel = this.page.getByLabel(label, { exact: false }).first();
      if (await byLabel.count()) {
        await byLabel.fill(value);
        return;
      }
    }
    if (placeholder) {
      const byPh = this.page.getByPlaceholder(placeholder, { exact: false }).first();
      if (await byPh.count()) {
        await byPh.fill(value);
        return;
      }
    }
    if (label) {
      const ctrl = this._afterLabel(label, "self::input or self::textarea");
      if (await ctrl.count()) {
        await ctrl.fill(value);
        return;
      }
    }
    if (id) {
      await this.page.locator(`#${id}`).first().fill(value);
      return;
    }
    throw new Error(`fill: no field matched (${label ?? placeholder ?? id})`);
  }

  // Select an <option>, by id or by the <select> following a label.
  async select(optionSpec, { id, label } = {}) {
    if (id) {
      await this.page.locator(`#${id}`).first().selectOption(optionSpec);
      return;
    }
    const byLabel = this.page.getByLabel(label, { exact: false }).first();
    if (await byLabel.count()) {
      await byLabel.selectOption(optionSpec);
      return;
    }
    const ctrl = this._afterLabel(label, "self::select");
    if (await ctrl.count()) {
      await ctrl.selectOption(optionSpec);
      return;
    }
    throw new Error(`select: no <select> matched (${label ?? id})`);
  }

  // Tick an option in a MultiSelect (inline checkbox list) by its visible text,
  // scoped to the multiselect with the given aria-label.
  async checkMultiOption(ariaLabel, optionText) {
    const container = this.page.locator(`[aria-label="${ariaLabel}"]`).first();
    if (!(await container.count())) return false;
    // Type into the filter box if present (shown when >6 options).
    const filter = container.locator("input:not([type=checkbox])").first();
    if (await filter.count()) await filter.fill(optionText).catch(() => {});
    const row = container
      .locator("label")
      .filter({ hasText: new RegExp(optionText, "i") })
      .first();
    if (!(await row.count())) return false;
    await row.locator('input[type="checkbox"]').check();
    return true;
  }

  // Locator for the first input/textarea/select following a (possibly
  // unassociated) <Label> whose text contains `text`.
  _afterLabel(text, selfPredicate) {
    const lit = JSON.stringify(text); // safe double-quoted xpath string
    const sibling = `xpath=//label[contains(normalize-space(.), ${lit})]/following-sibling::*[${selfPredicate}][1]`;
    return this.page.locator(sibling).first();
  }

  // Assert that some text becomes visible (e.g. a success toast or a new row).
  async expectText(text, { timeout } = {}) {
    await this.page
      .getByText(text, { exact: false })
      .first()
      .waitFor({ state: "visible", timeout: timeout ?? config.defaultTimeout });
  }

  // ---- reporting ---------------------------------------------------------

  summary() {
    const counts = { pass: 0, warn: 0, fail: 0, skip: 0 };
    for (const f of this.features) {
      for (const s of f.steps) counts[s.status] = (counts[s.status] ?? 0) + 1;
    }
    return counts;
  }

  writeReports() {
    const counts = this.summary();
    const payload = {
      runId: config.runId,
      baseUrl: config.baseUrl,
      username: config.username,
      startedAt: this._startedAt,
      finishedAt: new Date().toISOString(),
      counts,
      features: this.features,
    };
    fs.mkdirSync(config.reportDir, { recursive: true });
    fs.writeFileSync(
      path.join(config.reportDir, "qa-report.json"),
      JSON.stringify(payload, null, 2),
    );
    fs.writeFileSync(path.join(config.reportDir, "qa-report.md"), this._markdown(counts));
  }

  _markdown(counts) {
    const icon = { pass: "✅", warn: "⚠️", fail: "❌", skip: "➖" };
    const lines = [];
    lines.push(`# OpsMender QA Walkthrough Report`);
    lines.push("");
    lines.push(`- **Run:** \`${config.runId}\``);
    lines.push(`- **Target:** ${config.baseUrl}`);
    lines.push(`- **User:** ${config.username}`);
    lines.push(`- **Finished:** ${new Date().toISOString()}`);
    lines.push(
      `- **Totals:** ${counts.pass} passed · ${counts.warn} warnings · ${counts.fail} failed · ${counts.skip} skipped`,
    );
    lines.push("");
    for (const f of this.features) {
      lines.push(`## ${f.title}`);
      lines.push("");
      for (const s of f.steps) {
        lines.push(`- ${icon[s.status]} **${s.name}** _(${s.durationMs}ms)_`);
        if (s.error) {
          const first = String(s.error).split("\n")[0];
          lines.push(`    - error: ${first}`);
        }
        for (const e of s.events) {
          const detail =
            e.kind === "http" ? `HTTP ${e.status} ${e.url}` : `${e.kind}: ${e.text}`;
          lines.push(`    - ${detail}`);
        }
        if (s.screenshot) lines.push(`    - screenshot: \`${s.screenshot}\``);
      }
      lines.push("");
    }
    return lines.join("\n");
  }

  async finish() {
    if (this.browser) await this.browser.close();
    this.writeReports();
    const c = this.summary();
    console.log(
      `\n══ Summary: ${c.pass} passed · ${c.warn} warn · ${c.fail} failed · ${c.skip} skipped`,
    );
    console.log(`   Report: ${path.relative(process.cwd(), config.reportDir)}/qa-report.md`);
    return c.fail > 0 ? 1 : 0;
  }
}

Harness.prototype._startedAt = new Date().toISOString();
