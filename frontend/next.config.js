// @ts-check

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Standalone output required for Amplify WEB_COMPUTE (SSR) hosting
  output: "standalone",
  reactStrictMode: false,
};

module.exports = nextConfig;
