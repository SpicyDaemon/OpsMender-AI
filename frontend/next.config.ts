import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Enables standalone output for Docker deployment.
  // Produces a minimal server bundle in .next/standalone/
  output: "standalone",
};

export default nextConfig;
