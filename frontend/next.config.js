// @ts-check
const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Static export for Amplify Hosting.
  output: "export",
  reactStrictMode: false,
  webpack(config) {
    // Ensure @/ alias resolves to the frontend directory root.
    config.resolve.alias = {
      ...config.resolve.alias,
      "@": path.resolve(__dirname),
    };
    return config;
  },
};

module.exports = nextConfig;
