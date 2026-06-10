# Screenshot sanitization requirements

All images in this directory are displayed publicly on the product showcase site.
Before replacing any placeholder with a real screenshot, verify the following:

## Must not appear in any screenshot

- Personal or work email addresses
- Real customer, user, or organization names
- API keys, tokens, secrets, passwords, or auth headers
- Private or internal domain names (e.g. `*.corp.internal`, `*.lan`)
- Local machine paths (e.g. `C:\Users\`, `/home/username/`)
- Competitor product names or logos
- Personally identifiable information (PII)
- Internal project codenames or unreleased feature names

## Use realistic but generic placeholder data

- Incident titles: generic service/infrastructure descriptions only
- Usernames: single first names (alice, bob, carol) or role labels (admin, on-call)
- Hostnames: generic (e.g. `api-gateway.prod.internal`, `k8s-prod-cluster`)
- Timestamps: synthetic — do not expose real incident timestamps

## Format

- 1200×750 px recommended (matches viewBox of SVG placeholders)
- PNG or SVG accepted; avoid JPEG for UI screenshots (compression artifacts on text)
- File names must match the paths referenced in `src/pages/index.astro`:
  - `incidents-admin.png`
  - `incidents-operator.png`
  - `incident-detail.png`
  - `ai-session.png`
  - `mcp-skills.png`
  - `people-rbac.png`

## How these were captured

The current images are **real captures of the dashboard**, not mockups. They are
produced from the seeded demo database (synthetic "Acme Corp" data — `@acme.com`
addresses, redacted tokens) so nothing real is exposed. To regenerate:

1. Seed a throwaway SQLite DB:
   `OPSMENDER_DATABASE_URL="sqlite+aiosqlite:///./opsmender_demo.db" uv run python scripts/seed_demo.py`
2. Start the backend against that DB on a free port (serves `frontend/out`).
3. Log in as `admin` / `admin123` (Admin view) and `priya` / `priya123` (Operator view),
   inject the token + `opsmender:theme=dark` into `localStorage`, and screenshot each route
   with Playwright at a 2× device scale for crisp, high-resolution output.

## Review checklist before committing

- [ ] No PII visible anywhere in the image (demo data uses `@acme.com` only)
- [ ] No secrets or tokens visible
- [ ] No competitor names or logos
- [ ] No internal domains or local paths
- [ ] Data is plausible but clearly synthetic (Acme Corp demo seed)
- [ ] Captured at 2× device scale (≈3200×2000 px) for retina sharpness
