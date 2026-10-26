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
  // The platform is the single door: it serves the CRM under /crm by proxying to the CRM's own
  // containers, so there is one origin (one Supabase login) and no separate proxy port.
  //   dev  : defaults to the local CRM dev servers (127.0.0.1:8080 / :8787).
  //   prod : enabled only when CRM_FRONTEND_ORIGIN + CRM_BACKEND_ORIGIN are set (e.g. the shared
  //          Docker network names http://crm-frontend:8080 / http://crm-backend:8787); otherwise
  //          /crm simply isn't served.
  async rewrites() {
    const dev = process.env.NODE_ENV !== "production";
    const crmFrontend = process.env.CRM_FRONTEND_ORIGIN ?? (dev ? "http://127.0.0.1:8080" : undefined);
    const crmBackend = process.env.CRM_BACKEND_ORIGIN ?? (dev ? "http://127.0.0.1:8787" : undefined);
    if (!crmFrontend || !crmBackend) return [];
    // Dev: the Vite dev server serves under its base (/crm/), so keep the prefix. Prod: the CRM's
    // nginx serves the build at its root, so strip /crm.
    const crmBase = dev ? `${crmFrontend}/crm` : crmFrontend;
    return [
      // The CRM calls /crm-api/api/v1/* ; strip /crm-api so the backend sees /api/v1/*.
      { source: "/crm-api/:path*", destination: `${crmBackend}/:path*` },
      { source: "/crm", destination: `${crmBase}/` },
      { source: "/crm/:path*", destination: `${crmBase}/:path*` },
    ];
  },
};

export default nextConfig;
