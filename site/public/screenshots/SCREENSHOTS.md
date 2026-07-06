# Screenshot capture requirements

Images in this directory may be displayed publicly in the root README and the
product showcase site. Before replacing or committing any screenshot, verify the
checklist below.

## Current README launch set

`scripts/take_screenshots.mjs` captures these four PNGs:

- `incidents-list.png` — incident command center
- `live-session-detail.png` — live AI session detail
- `approvals-pending.png` — Tier 1 approval inbox with pending work
- `settings.png` — workspace settings and guardrails

The showcase site may also reference older gallery filenames:

- `incidents-admin.png`
- `incidents-operator.png`
- `incident-detail.png`
- `ai-session.png`
- `mcp-skills.png`
- `people-rbac.png`

## Capture command

1. Start a cleaned, synthetic demo instance on `http://localhost:8000`.
2. Ensure the local environment has either `OPSMENDER_EMAIL` /
   `OPSMENDER_PASSWORD` or `OPSMENDER_BOOTSTRAP_ADMIN_EMAIL` /
   `OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD` available. The script also reads those
   bootstrap values from the repository `.env` file.
3. Run:

```bash
node scripts/take_screenshots.mjs
```

Optional overrides:

```bash
OPSMENDER_BASE_URL=http://localhost:8000 \
OPSMENDER_EMAIL=admin@example.com \
OPSMENDER_PASSWORD='<password>' \
node scripts/take_screenshots.mjs
```

The default output directory is `site/public/screenshots/`.

## Must not appear in any screenshot

- Personal or work email addresses
- Real customer, user, or organization names
- API keys, tokens, secrets, passwords, or auth headers
- Private or internal domain names
- Local machine paths
- Competitor product names or logos
- Personally identifiable information
- Internal project codenames or unreleased feature names

## Use realistic but generic placeholder data

- Incident titles: generic service or infrastructure descriptions only
- Usernames: single first names or role labels
- Hostnames: generic infrastructure names
- Timestamps: synthetic demo data only

## Review checklist before committing

- [ ] No PII visible anywhere
- [ ] No secrets or tokens visible
- [ ] No competitor names or logos
- [ ] No internal domains or local paths
- [ ] Data is plausible but clearly synthetic
- [ ] The Approvals screenshot shows pending work, not the empty state
