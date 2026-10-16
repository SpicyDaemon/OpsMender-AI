import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export - the Python backend serves the built files from `out/`.
  // One process, one container, one binary.
  output: "export",
  images: { unoptimized: true },
  // Since 16.3, `next dev` can write a managed block into AGENTS.md when it
  // detects certain development tools; keep the repository free of it.
  agentRules: false,
  experimental: {
    // Since 16.4, `next build` can look up newer releases and stop a scripted
    // build to recommend one; the npm audit step in CI reports advisories.
    agentUpgrade: false,
  },
};

export default nextConfig;
