import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { PlanReview } from "@/components/plan/plan-review";
import { PlanRunner } from "@/components/plan/plan-runner";
import { getPlan, getPlanProgress, loadPlanInternal, planFooter } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";
import { buildPlanDocument } from "@/lib/plan/document";
import type { PlanStatus } from "@/lib/plan/types";
import { currentStep, reachedStep } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";

export const metadata: Metadata = { title: "Plan" };

const VERSION_NOTES: Partial<Record<PlanStatus, string>> = {
  failed: " (failed)",
  blocked: " (blocked)",
};

export default async function PlanPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ v?: string }>;
}) {
  const { id } = await params;
  const { v } = await searchParams;
  const { supabase, project } = await requireProject(id);
  if (reachedStep(project.status) < 3) {
    redirect(`/projects/${project.id}/${currentStep(project.status).path}`);
  }

  const { data } = await supabase
    .from(TABLES.plans)
    .select("id, version, status")
    .eq("project_id", project.id)
    .order("version", { ascending: false })
    .returns<{ id: string; version: number; status: PlanStatus }[]>();
  const plans = data ?? [];
  if (!plans.length) return <PlanRunner projectId={project.id} initial={null} />;

  const latest = plans[0];
  const selected = plans.find((p) => String(p.version) === v) ?? latest;
  const plan = await getPlan(supabase, selected.id);
  if (!plan) redirect(`/projects/${project.id}/plan`);

  if (plan.status === "ready") {
    const { internal, model } = await loadPlanInternal(supabase, plan);
    return (
      <PlanReview
        key={plan.id}
        projectId={project.id}
        planId={plan.id}
        version={plan.version}
        versions={plans.map((p) => ({
          version: p.version,
          label: `Version ${p.version}${VERSION_NOTES[p.status] ?? (p.status === "ready" ? "" : " (running)")}`,
        }))}
        doc={buildPlanDocument({
          customerName: project.customer_name,
          diagnosis: plan.diagnosis,
          strategy: plan.strategy,
          execution: plan.execution,
          internal,
        })}
        footer={planFooter(plan, model)}
      />
    );
  }

  const earlierReady = plans.filter((p) => p.status === "ready" && p.id !== plan.id);
  return (
    <div className="grid gap-6">
      <PlanRunner
        key={plan.id}
        projectId={project.id}
        // Only the latest plan can be resumed or unblocked; older ones are shown as they ended.
        initial={await getPlanProgress(supabase, plan)}
      />
      {earlierReady.length > 0 && (
        <p className="text-sm text-muted-foreground print:hidden">
          Finished versions:{" "}
          {earlierReady.map((p, i) => (
            <span key={p.id}>
              {i > 0 && ", "}
              <Link
                href={`/projects/${project.id}/plan?v=${p.version}`}
                className="text-foreground underline underline-offset-4"
              >
                version {p.version}
              </Link>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
