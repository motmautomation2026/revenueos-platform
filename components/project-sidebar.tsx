"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  Activity,
  FileText,
  FileUp,
  LayoutDashboard,
  ListChecks,
  Lock,
  Mail,
  Phone,
  Search,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type SidebarBadges = {
  /** Decisions waiting for a person, shown on Dashboard. */
  waiting: number;
  /** Of those, the ones that are tasks for a person, shown on People's tasks. */
  waitingPeople: number;
  running: boolean;
  /** A tool's result so far in words, e.g. "147 companies · 60 contacts". Null: not in this month's plan. */
  research: string | null;
  email: string | null;
  calls: string | null;
};

type Item = {
  key: string;
  href: string;
  label: string;
  hint: string;
  icon: LucideIcon;
  state: "open" | "done" | "locked";
  /** A live result shown in place of the hint, e.g. "147 companies". */
  result?: string | null;
  badge?: { text: string; tone: "attention" } | null;
  live?: boolean;
};

/**
 * The project's left panel: Planning (how the plan is built), then Execution (the Brain's
 * live work) with one entry per tool. On small screens it becomes a scrolling strip.
 */
export function ProjectSidebar({
  projectId,
  reached,
  planReady,
  badges,
}: {
  projectId: string;
  /** Highest planning step reached: 1 inputs, 2 checklist, 3 plan, 4 plan finished. */
  reached: number;
  planReady: boolean;
  badges: SidebarBadges | null;
}) {
  const pathname = usePathname();
  const run = useSearchParams().get("run");
  const base = `/projects/${projectId}`;
  const runQuery = run ? `?run=${encodeURIComponent(run)}` : "";
  const execution = planReady ? "open" : "locked";
  // Before any run, a tool says what it is; once there is a run, what it has done so far.
  const toolHint = (name: string, result: string | null | undefined) =>
    badges ? { hint: name, result: result ?? "Not in this month's plan" } : { hint: name };

  const groups: { title: string; note?: string; items: Item[] }[] = [
    {
      title: "Planning",
      items: [
        {
          key: "upload",
          href: `${base}/upload`,
          label: "Inputs",
          hint: "Customer documents",
          icon: FileUp,
          state: reached > 1 ? "done" : "open",
        },
        {
          key: "checklist",
          href: `${base}/checklist`,
          label: "Checklist",
          hint: "Discovery questions",
          icon: ListChecks,
          state: reached < 2 ? "locked" : reached > 2 ? "done" : "open",
        },
        {
          key: "plan",
          href: `${base}/plan`,
          label: "GTM plan",
          hint: "Diagnosis, strategy, execution plan",
          icon: FileText,
          state: reached < 3 ? "locked" : planReady ? "done" : "open",
        },
      ],
    },
    {
      title: "Execution",
      note: planReady ? undefined : "Opens when the plan is ready",
      items: [
        {
          key: "dashboard",
          href: `${base}/execution${runQuery}`,
          label: "Dashboard",
          hint: "Live progress and analysis",
          icon: LayoutDashboard,
          state: execution,
          badge: badges?.waiting ? { text: String(badges.waiting), tone: "attention" } : null,
          live: badges?.running,
        },
        {
          key: "people",
          href: `${base}/execution/people${runQuery}`,
          label: "People's tasks",
          hint: "Work done by the team",
          icon: UsersRound,
          state: execution,
          badge: badges?.waitingPeople ? { text: String(badges.waitingPeople), tone: "attention" } : null,
        },
      ],
    },
    {
      title: "Tools",
      items: [
        {
          key: "research",
          href: `${base}/execution/research${runQuery}`,
          label: "Data research",
          ...toolHint("QuickICP", badges?.research),
          icon: Search,
          state: execution,
        },
        {
          key: "email",
          href: `${base}/execution/email${runQuery}`,
          label: "Email",
          ...toolHint("Email tool", badges?.email),
          icon: Mail,
          state: execution,
        },
        {
          key: "calls",
          href: `${base}/execution/calls${runQuery}`,
          label: "Calls",
          ...toolHint("Quickhowl", badges?.calls),
          icon: Phone,
          state: execution,
        },
        {
          key: "activity",
          href: `${base}/execution/activity${runQuery}`,
          label: "Activity log",
          hint: "Every step, newest first",
          icon: Activity,
          state: execution,
        },
      ],
    },
  ];

  return (
    <nav
      aria-label="Project sections"
      className="flex gap-1 overflow-x-auto rounded-xl border bg-background p-2 lg:sticky lg:top-18 lg:grid lg:gap-0 lg:overflow-visible lg:p-2 print:hidden"
    >
      {groups.map((group, index) => (
        <div
          key={group.title}
          className={cn(
            "flex shrink-0 gap-1 lg:grid lg:min-w-0 lg:gap-0.5 lg:px-1 lg:py-3",
            index > 0 && "lg:border-t"
          )}
        >
          <p className="hidden px-2 pb-1.5 text-[0.6875rem] font-semibold tracking-wider text-muted-foreground uppercase lg:block">
            {group.title}
          </p>
          {group.items.map((item) => {
            const path = item.href.split("?")[0];
            const active = pathname === path;
            const locked = item.state === "locked";
            const Icon = locked ? Lock : item.icon;

            const body = (
              <>
                <span
                  className={cn(
                    "relative flex size-7 shrink-0 items-center justify-center rounded-lg",
                    active ? "bg-primary text-primary-foreground" : locked ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary"
                  )}
                >
                  <Icon className="size-4" aria-hidden />
                </span>
                <span className="min-w-0 lg:flex-1">
                  <span
                    className={cn(
                      "block truncate text-sm leading-tight whitespace-nowrap",
                      active ? "font-semibold text-primary" : "font-medium"
                    )}
                  >
                    {item.label}
                  </span>
                  <span
                    className="hidden truncate text-xs leading-snug text-muted-foreground lg:block"
                    title={item.result ? `${item.hint}: ${item.result}` : item.hint}
                  >
                    {item.result ?? item.hint}
                  </span>
                </span>
                {item.live && (
                  <span className="size-2 shrink-0 animate-pulse rounded-full bg-emerald-600" title="Running now" />
                )}
                {item.badge && (
                  <span
                    className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-amber-500 px-1.5 text-xs font-semibold text-white tabular-nums"
                    title="Waiting for a person"
                  >
                    {item.badge.text}
                  </span>
                )}
              </>
            );
            const className = cn(
              "relative flex min-w-0 items-center gap-2.5 rounded-lg px-2 py-1.5 outline-none",
              // The current page: tinted, with a bar on the left edge.
              active && "bg-primary/10 lg:before:absolute lg:before:inset-y-1.5 lg:before:-left-1 lg:before:w-1 lg:before:rounded-full lg:before:bg-primary",
              locked ? "cursor-not-allowed opacity-60" : "hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
            );

            return locked ? (
              <span key={item.key} className={className} aria-disabled title={group.note ?? "Finish the previous step first"}>
                {body}
              </span>
            ) : (
              <Link key={item.key} href={item.href} className={className} aria-current={active ? "page" : undefined}>
                {body}
              </Link>
            );
          })}
          {group.note && <p className="hidden px-2 pt-1 text-xs text-muted-foreground lg:block">{group.note}</p>}
        </div>
      ))}
    </nav>
  );
}
