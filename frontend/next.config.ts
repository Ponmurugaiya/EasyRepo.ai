import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["172.19.16.1"],
  // Disable React Strict Mode to prevent React Flow's nodeTypes/edgeTypes
  // warning (#002) in development. Strict Mode double-invokes component
  // renders which causes React Flow to see "new" object references on every
  // render even when constants are defined outside the component.
  // This only affects dev behavior — production is unaffected.
  reactStrictMode: false,
};

export default nextConfig;
