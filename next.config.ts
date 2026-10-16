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
};

export default nextConfig;
