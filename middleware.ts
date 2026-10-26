import type { NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/middleware";

export async function middleware(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  matcher: [
    // Everything except Next internals, static assets, and the CRM paths (/crm, /crm-api) — those
    // are proxied to the CRM, which handles its own auth, so the platform's session check must not
    // intercept or redirect them.
    "/((?!_next/static|_next/image|favicon.ico|crm|crm-api|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
