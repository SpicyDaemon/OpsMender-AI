# OpsMender Manual-QA Walkthrough (Playwright)

An end-to-end **manual-QA walkthrough** that drives the real operator console
in a browser the way a person would: it logs in, sets up a team, a service, an
escalation policy and an on-call roster, opens the roster calendar, exercises
the notifications surface, creates and resolves an incident, creates an SLA
target, runs the AI model "Test connection", and finally logs out — capturing
**every console error, unhandled page error, and HTTP 5xx** along the way and
writing a pass/fail report.

This is intentionally separate from the unit/component tests (`pytest`,
`vitest`). Those check pieces in isolation; this checks that the whole
workflow actually works against a running instance.

## What it covers

| Feature | Steps |
|---|---|
| Authentication | login page renders · sign in · session established |
| Teams | page loads · create team (with the current user as a member) |
| Services | page loads · create service on the team |
| Escalation policies | page loads · create escalation chain |
| Rosters | page loads · create weekly on-call roster |
| Roster calendar | open calendar · navigate range |
| Notifications | page loads · open Add-Channel form · *(optional)* send live test |
| Incidents | page loads · create (fire-test) · open detail · acknowledge · resolve |
| Reliability | page loads · create HTTP SLA target |
| AI models | page loads · *(optional)* create config · test connection |
| Skills | page loads |
| Authentication | sign out · session cleared |

Each step uses **soft assertions**: a failure is recorded with a full-page
screenshot and the run keeps going, so one report shows everything that is or
isn't working. Steps that can't run (missing precondition, opt-in disabled)
are marked **skipped**, not failed.

## Prerequisites

1. A running OpsMender instance you can reach (UI + API on the same origin).
2. Node.js 18+.
3. Playwright's Chromium browser:

   ```bash
   cd qa
   npm install            # links Playwright from ../frontend/node_modules
   npx playwright install chromium
   ```

   If your environment can't reach the Playwright CDN, point the suite at an
   existing Chrome/Chromium with `QA_CHROMIUM_PATH=/path/to/chrome`.

## Run it

```bash
cd qa
QA_BASE_URL=http://localhost:8000 \
QA_USERNAME=admin QA_PASSWORD=admin123 \
node run-qa.mjs
```

Watch it run in a real window:

```bash
QA_HEADLESS=false QA_SLOWMO=200 node run-qa.mjs
```

Run only some features:

```bash
QA_FEATURES=auth,incidents,reliability node run-qa.mjs
```

The process exits non-zero if any step **failed** (warnings/skips don't fail
the run), so it slots into CI if you want.

## Parameters

All configuration is via environment variables (or a gitignored
`qa/qa.config.json`; see `qa.config.example.json`). Env vars win over the file.

| Env var | Default | Meaning |
|---|---|---|
| `QA_BASE_URL` | `http://localhost:8000` | App + API origin. |
| `QA_USERNAME` / `QA_PASSWORD` | `admin` / `admin123` | Login credentials. |
| `QA_HEADLESS` | `true` | Set `false` to watch the browser. |
| `QA_SLOWMO` | `0` | Milliseconds to slow each action (with `QA_HEADLESS=false`). |
| `QA_TIMEOUT` | `20000` | Default per-action timeout (ms). |
| `QA_CHROMIUM_PATH` | _(bundled)_ | Path to a Chrome/Chromium executable. |
| `QA_RUN_ID` | `QA-<timestamp>` | Prefix stamped on every created entity. |
| `QA_CLEANUP` | `false` | Best-effort delete of this run's QA-prefixed entities at the end. |
| `QA_FIRE_TEST_INCIDENT` | `true` | Use the synthetic Fire-Test-Incident flow (vs a real incident). |
| `QA_SEND_TEST_NOTIFICATION` | `false` | Actually send a live test notification (**may page real people**). |
| `QA_CREATE_MODEL` | `false` | Create a model config during the run (needs the `QA_MODEL_*` params). |
| `QA_TEST_MODEL_CONNECTION` | `true` | Click "Test" on the first saved model config. |
| `QA_MODEL_PROVIDER` / `QA_MODEL_ID` / `QA_MODEL_KEY_ENV` / `QA_MODEL_BASE_URL` | openai / gpt-4o-mini / OPENAI_API_KEY / — | Params for `QA_CREATE_MODEL`. |
| `QA_FEATURES` | _(all)_ | Comma-separated feature ids to run. |
| `QA_REPORT_DIR` | `qa/report` | Where reports + screenshots are written. |

## Output

Written to `qa/report/` (gitignored):

- `qa-report.md` — human-readable per-step results, with captured errors and
  screenshot paths for failures.
- `qa-report.json` — the same data, machine-readable.
- `screenshots/` — full-page screenshots captured at each failing step.

## Safety notes

- The walkthrough **creates real data** (a team, service, chain, roster, SLA
  target, and a — by default synthetic — incident) in whatever instance you
  point it at. Everything is named with the `QA_RUN_ID` prefix so it's easy to
  find and delete. Set `QA_CLEANUP=true` to remove it automatically.
- Pointing it at a **production** instance can page on-call people (a real
  incident, or `QA_SEND_TEST_NOTIFICATION=true`). Prefer a dev/staging
  instance, and keep `QA_FIRE_TEST_INCIDENT=true` (the default) so incidents
  are synthetic.
- `QA_TEST_MODEL_CONNECTION` makes a live call to whatever provider the first
  model config uses. A failed connection is still reported as a *pass* of the
  round-trip (the UI surfaced a result); it only fails if the UI never
  responds.

## Layout

```
qa/
  run-qa.mjs            entry point / orchestrator
  lib/
    config.mjs          parameters (env + qa.config.json)
    harness.mjs         browser, error capture, step runner, report
    api.mjs             login bootstrap + best-effort cleanup
  features/
    index.mjs           ordered feature list
    auth.mjs teams.mjs services.mjs escalation.mjs rosters.mjs
    roster_calendar.mjs notifications.mjs incidents.mjs
    reliability.mjs models.mjs skills.mjs logout.mjs
```

Add a feature by dropping a `features/<name>.mjs` that default-exports
`{ id, title, run(h) }` and listing it in `features/index.mjs`. Use
`h.step(name, fn)` for each check and `Harness.skip(reason)` to skip.
