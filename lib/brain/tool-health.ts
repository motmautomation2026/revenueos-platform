import "server-only";

/**
 * Reachability pre-flight for the external tools (Phase 2a). The platform does NOT hold the tools'
 * credentials — those live in the Brain's env — so this only answers "is the service up?", not "is it
 * configured right". Any HTTP reply (even 401/404) means the host is reachable; only a network error or
 * timeout means it is down. The deeper creds/config check (check_system.py) is surfaced separately (2b).
 *
 * Hosts come from env so they can change without a code edit; the defaults are the current b2botix hosts.
 */
export type ToolReach = {
  key: string;
  label: string;
  url: string;
  state: "up" | "degraded" | "down";
  status: number | null;
  ms: number;
  detail: string;
};

const TOOLS = [
  { key: "data", label: "Data (QuickICP / n8n)", url: process.env.QUICKICP_HEALTH_URL ?? "https://n8n.b2botix.ai" },
  { key: "email", label: "Email (Outly)", url: process.env.EMAIL_TOOL_HEALTH_URL ?? "https://revenueosemial.b2botix.ai" },
  { key: "calls", label: "Calls (Quickhowl)", url: process.env.QUICKHOWL_HEALTH_URL ?? "https://revenueoscall.b2botix.ai" },
];

const TIMEOUT_MS = 6000;

async function ping(url: string): Promise<{ state: ToolReach["state"]; status: number | null; ms: number; detail: string }> {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    // GET (not HEAD): some hosts reject HEAD. We only care that an HTTP response comes back.
    const res = await fetch(url, { method: "GET", redirect: "manual", signal: controller.signal, cache: "no-store" });
    const ms = Date.now() - started;
    if (res.status >= 500) return { state: "degraded", status: res.status, ms, detail: `Responding but erroring (HTTP ${res.status}).` };
    return { state: "up", status: res.status, ms, detail: `Reachable (HTTP ${res.status}, ${ms} ms).` };
  } catch (error) {
    const ms = Date.now() - started;
    const aborted = error instanceof Error && error.name === "AbortError";
    return { state: "down", status: null, ms, detail: aborted ? `No response within ${TIMEOUT_MS / 1000}s.` : "Could not connect." };
  } finally {
    clearTimeout(timer);
  }
}

/** Ping every tool host in parallel. Safe: GET only, no credentials, no side effects. */
export async function pingTools(): Promise<ToolReach[]> {
  return Promise.all(
    TOOLS.map(async (tool) => {
      const result = await ping(tool.url);
      return { key: tool.key, label: tool.label, url: tool.url, ...result };
    })
  );
}
