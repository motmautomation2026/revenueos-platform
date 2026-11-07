"use client";

import { useMemo, useState } from "react";
import { Loader2, Mail, Phone } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { TestProject, TestResult } from "@/lib/tools/test-tools";

type Run = (input: { projectId: string; target: string }) => Promise<TestResult>;

export function TestToolsPanel({ projects, call, email }: { projects: TestProject[]; call: Run; email: Run }) {
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const selected = useMemo(() => projects.find((p) => p.id === projectId) ?? null, [projects, projectId]);

  if (projects.length === 0) {
    return <p className="text-sm text-muted-foreground">You have no projects to test. Create one first.</p>;
  }

  return (
    <div className="grid gap-4">
      <label className="grid max-w-md gap-1 text-sm">
        <span className="font-medium">Project</span>
        <select
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          className="h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">Tests use this project&apos;s configured sender and call line.</span>
      </label>

      {selected && (
        <div className="grid gap-3 lg:grid-cols-2">
          <TestCard
            icon={Mail}
            title="Email"
            using={selected.emailSender ? `Sends as ${selected.emailSender}` : "No sender set for this project."}
            ready={!!selected.emailSender}
            inputType="email"
            placeholder="you@yourmail.com"
            actionLabel="Send test"
            run={(target) => email({ projectId: selected.id, target })}
          />
          <TestCard
            icon={Phone}
            title="Calls"
            using={selected.callLine ?? "No calling account / line set for this project."}
            ready={selected.callLineReady}
            inputType="tel"
            placeholder="+9198XXXXXXXX"
            actionLabel="Call me"
            run={(target) => call({ projectId: selected.id, target })}
          />
        </div>
      )}
    </div>
  );
}

function TestCard({
  icon: Icon,
  title,
  using,
  ready,
  inputType,
  placeholder,
  actionLabel,
  run,
}: {
  icon: typeof Mail;
  title: string;
  using: string;
  ready: boolean;
  inputType: "email" | "tel";
  placeholder: string;
  actionLabel: string;
  run: (target: string) => Promise<TestResult>;
}) {
  const [target, setTarget] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  const onRun = async () => {
    if (!target.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      setResult(await run(target.trim()));
    } catch {
      toast.error("The test could not run. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid content-start gap-3 rounded-xl border p-4">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" aria-hidden />
        <span className="font-medium">{title}</span>
        <span className={cn("ml-auto size-2.5 rounded-full", ready ? "bg-emerald-500" : "bg-muted-foreground/40")} aria-hidden />
      </div>
      <p className="text-sm text-muted-foreground">{using}</p>

      <div className="flex gap-2">
        <Input type={inputType} value={target} onChange={(e) => setTarget(e.target.value)} placeholder={placeholder} aria-label={`Your ${title.toLowerCase()} for the test`} />
        <Button type="button" onClick={onRun} disabled={loading || !target.trim()}>
          {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {actionLabel}
        </Button>
      </div>

      {result && (
        <p
          className={cn(
            "rounded-lg border px-3 py-2 text-sm",
            result.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800"
              : result.pending
                ? "text-muted-foreground"
                : "border-destructive/30 bg-destructive/5 text-destructive"
          )}
        >
          {result.detail}
        </p>
      )}
    </div>
  );
}
