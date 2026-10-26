import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Node-only document parsers: load them from node_modules instead of bundling.
  // tesseract.js runs in a worker thread and @napi-rs/canvas is a native module (free OCR, lib/files/ocr.ts).
  serverExternalPackages: ["exceljs", "mammoth", "tesseract.js", "@napi-rs/canvas"],
  // The agent prompts and schemas are read from disk at run time.
  outputFileTracingIncludes: {
    "/**": ["./prompts/**", "./schemas/**", "./node_modules/@tesseract.js-data/eng/4.0.0_best_int/**"],
  },
  experimental: {
    // The filled checklist is uploaded through a server action.
    serverActions: { bodySizeLimit: "5mb" },
  },
  // Local dev only: make http://localhost:3000 the single origin for the platform AND the CRM, so
  // the CRM shares the platform's Supabase cookie (no second login) without needing a reverse proxy.
  // In production Caddy does this instead (deploy/Caddyfile.snippet), so these are skipped there.
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    const crmFrontend = process.env.CRM_FRONTEND_ORIGIN ?? "http://127.0.0.1:8080";
    const crmBackend = process.env.CRM_BACKEND_ORIGIN ?? "http://127.0.0.1:8787";
    return [
      // The CRM frontend calls /crm-api/api/v1/* ; strip /crm-api so the backend sees /api/v1/*.
      { source: "/crm-api/:path*", destination: `${crmBackend}/:path*` },
      // The CRM Vite dev server serves under its base (/crm/), so keep the prefix.
      { source: "/crm", destination: `${crmFrontend}/crm/` },
      { source: "/crm/:path*", destination: `${crmFrontend}/crm/:path*` },
    ];
  },
};

export default nextConfig;
