# OpsMender — Product Showcase Site

Static marketing/showcase site built with [Astro](https://astro.build) + [Tailwind CSS](https://tailwindcss.com).
Deployed to GitHub Pages from the `main` branch.

## Local development

```bash
cd site
npm install
npm run dev        # http://localhost:4321
```

## Production build

```bash
cd site
npm run build      # outputs to site/dist/
npm run preview    # preview the built site locally
```

## GitHub Pages deployment

The workflow at `.github/workflows/deploy-site.yml` runs on every push to `main`.
It builds the site with `SITE_BASE=/OpsMender-AI` (the repository name) and
deploys the output to the `gh-pages` environment.

Enable Pages in your repository settings:

1. **Settings → Pages → Source**: select `GitHub Actions`
2. The workflow handles everything else automatically

The live site will be available at:
`https://<org-or-user>.github.io/OpsMender-AI/`

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SITE_BASE` | `/` | Base path — set to repo name for project Pages |
| `SITE_URL` | `https://example.com` | Canonical origin for OG/meta tags |

## Screenshot placeholders

`public/screenshots/` contains SVG mockups for the screenshot gallery.
See `public/screenshots/SCREENSHOTS.md` for sanitization requirements before
replacing with real screenshots.

## Customization

- **Colors / design tokens**: `tailwind.config.mjs`
- **Page content**: `src/pages/index.astro`
- **HTML shell / fonts / meta**: `src/layouts/Layout.astro`
