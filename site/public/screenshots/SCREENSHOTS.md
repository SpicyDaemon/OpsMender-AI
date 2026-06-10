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
  - `incidents.png` (or `.svg`)
  - `session-chat.png` (or `.svg`)
  - `mcp-skills.png` (or `.svg`)
  - `reliability.png` (or `.svg`)
  - `maintenance-windows.png` (or `.svg`)

## Review checklist before committing

- [ ] No PII visible anywhere in the image
- [ ] No secrets or tokens visible
- [ ] No competitor names or logos
- [ ] No internal domains or local paths
- [ ] Placeholder data is plausible but clearly synthetic
- [ ] Image is 1200×750 px (or equivalent aspect ratio)
