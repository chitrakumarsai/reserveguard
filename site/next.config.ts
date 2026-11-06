import type { NextConfig } from "next";

// Static export: the site is plain HTML, CSS and the kit's scripts, served from any static host.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
