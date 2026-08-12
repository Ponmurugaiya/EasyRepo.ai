import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Static export for Amplify Hosting deployment.
  // The app is fully client-side — no server API routes.
  output: "export",
  // Disable React Strict Mode to prevent React Flow's nodeTypes/edgeTypes
  // warning (#002) in development.
  reactStrictMode: false,
};

export default nextConfig;
