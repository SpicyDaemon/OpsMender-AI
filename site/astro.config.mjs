import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// For GitHub Pages project sites (e.g. https://username.github.io/repo-name),
// set SITE_BASE env var to the repo name in your workflow:
//   env:
//     SITE_BASE: /OpsMender-AI
//
// For custom domains or username.github.io root, leave SITE_BASE unset (defaults to /).
const base = process.env.SITE_BASE ?? '/';
const site = process.env.SITE_URL ?? 'https://example.com';

export default defineConfig({
  integrations: [tailwind()],
  output: 'static',
  base,
  site,
});
