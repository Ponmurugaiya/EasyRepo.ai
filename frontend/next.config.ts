import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Static export for Amplify Hosting.
  output: "export",
  // Disable Turbopack for production builds — use webpack for stable output.
  turbopack: undefined,
  reactStrictMode: false,
};

export default nextConfig;
