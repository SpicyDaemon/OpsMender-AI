import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export — the Python backend serves the built files from `out/`.
  // One process, one container, one binary.
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
