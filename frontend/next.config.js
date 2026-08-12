// @ts-check
const path = require("path");

// Debug: log __dirname so we can verify the path in Amplify logs
console.log("[next.config.js] __dirname:", __dirname);

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  output: "export",
  reactStrictMode: false,
  webpack(config, { buildId, dev, isServer, defaultLoaders, nextRuntime, webpack }) {
    const frontendDir = __dirname;
    console.log("[webpack config] frontendDir:", frontendDir);
    
    // Override @/ alias to point to the frontend directory
    if (config.resolve && config.resolve.alias) {
      config.resolve.alias["@"] = frontendDir;
    } else {
      config.resolve = config.resolve || {};
      config.resolve.alias = { "@": frontendDir };
    }
    console.log("[webpack config] @ alias set to:", frontendDir);
    return config;
  },
};

module.exports = nextConfig;
