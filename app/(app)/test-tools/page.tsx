import type { Metadata } from "next";
import { requireUser } from "@/lib/auth";
import { loadTestProjects } from "@/lib/tools/test-tools";
import { TestToolsPanel } from "@/components/tools/test-tools-panel";
import { testCall, testEmail } from "./actions";

export const metadata: Metadata = { title: "Test tools" };

/**
 * Standalone tool testing — independent of any project's run/execution. Pick a project, then send a test
 * to your own inbox or place a test call to your own number, using that project's configured sender/line.
 * The call test is live; the email test is coming next (needs the sender/SMTP wiring).
 */
export default async function TestToolsPage() {
  const { user } = await requireUser();
  const projects = await loadTestProjects(user.id);

  return (
    <div className="mx-auto grid w-full max-w-4xl gap-6 px-4 py-6">
      <header className="grid gap-1">
        <h1 className="text-xl font-semibold tracking-tight">Test tools</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Send yourself a real test, any time — independent of a project&apos;s run. Pick a project to use its
          configured sender and call line, then send a test to your own inbox or place a test call to your own number.
        </p>
      </header>
      <TestToolsPanel projects={projects} call={testCall} email={testEmail} />
    </div>
  );
}
