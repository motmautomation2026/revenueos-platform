import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Node-only document parsers: load them from node_modules instead of bundling.
  serverExternalPackages: ["exceljs", "mammoth"],
  // The agent prompts and schemas are read from disk at run time.
  outputFileTracingIncludes: {
    "/**": ["./prompts/**", "./schemas/**"],
  },
  experimental: {
    // The filled checklist is uploaded through a server action.
    serverActions: { bodySizeLimit: "5mb" },
  },
};

export default nextConfig;
