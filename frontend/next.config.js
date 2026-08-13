// @ts-check

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Static export — fully client-side app, no server routes.
  // Output goes to out/ directory for Amplify WEB hosting.
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: false,
};

module.exports = nextConfig;
