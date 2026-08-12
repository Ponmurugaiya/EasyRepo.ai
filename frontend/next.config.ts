import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Static export for Amplify Hosting.
  output: "export",
  reactStrictMode: false,
  webpack(config) {
    // Ensure @/ alias resolves to the frontend directory root
    // regardless of the working directory during the build.
    config.resolve.alias = {
      ...config.resolve.alias,
      "@": path.resolve(__dirname),
    };
    return config;
  },
};

export default nextConfig;
