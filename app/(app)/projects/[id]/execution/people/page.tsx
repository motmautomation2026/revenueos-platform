import type { Metadata } from "next";
import { UserRound } from "lucide-react";
import { DecisionList } from "@/components/brain/decision-list";
import {
  EmptyTool,
  ExecutionEmpty,
  ExecutionHeader,
  Pill,
  SectionTitle,
  StatTile,
  TASK_STATUS,
  dateTime,
  number,
} from "@/components/brain/parts";
import type { TaskCard } from "@/lib/brain/view";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "People's tasks" };

const HUMAN_KINDS = ["human_task", "human_gate_task"];

export default async function PeoplePage(props: ExecutionPageProps) {
  const { project, data, base } = await loadExecutionPage(props);
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;

  const { view, runs, rows } = data;
  const cards = view.columns.flatMap((column) => column.cards).filter((card) => card.actor === "person");
  const waiting = view.openDecisions.filter((d) => HUMAN_KINDS.includes(d.kind));
  const waitingTasks = new Set(waiting.map((d) => d.taskId));

  // The latest answer a person gave for each task: who, when and their note.
  const answerOf = new Map<number, { answer: string; reason: string; at: string | null }>();
  for (const decision of rows.decisions) {
    if (decision.task_row_id != null && HUMAN_KINDS.includes(decision.kind) && decision.status === "answered") {
      answerOf.set(decision.task_row_id, {
        answer: decision.answer ?? "",
        reason: decision.reason ?? "",
        at: decision.answered_at,
      });
    }
  }

  const byRole = new Map<string, TaskCard[]>();
  for (const card of cards) byRole.set(card.agent, [...(byRole.get(card.agent) ?? []), card]);
  const done = cards.filter((c) => c.status === "done").length;
  const upNext = cards.filter((c) => c.status === "pending").length;

  return (
    <div className="grid gap-8">
      <ExecutionHeader title="People's tasks" view={view} runs={runs} basePath={`${base}/people`} />

      {cards.length === 0 && <EmptyTool>This month plan has no tasks for people.</EmptyTool>}

      {cards.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile label="Waiting for a person now" value={number.format(waiting.length)} attention={waiting.length > 0} />
          <StatTile label="Up next" value={number.format(upNext)} />
          <StatTile
            label="Done"
            value={number.format(done)}
            of={number.format(cards.length)}
            percent={Math.round((done / cards.length) * 100)}
          />
        </div>
      )}

      {waiting.length > 0 && (
        <section className="grid gap-3">
          <SectionTitle>Waiting for a person</SectionTitle>
          <DecisionList projectId={project.id} decisions={waiting} />
        </section>
      )}

      {[...byRole.entries()].map(([role, roleCards]) => (
        <section key={role} className="grid gap-3">
          <SectionTitle aside={`${roleCards.filter((c) => c.status === "done").length} of ${roleCards.length} done`}>
            <span className="inline-flex items-center gap-2">
              <UserRound className="size-4 text-muted-foreground" aria-hidden />
              {role}
            </span>
          </SectionTitle>
          <ul className="grid gap-3 xl:grid-cols-2">
            {roleCards.map((card) => {
              const status = TASK_STATUS[card.status];
              const answer = answerOf.get(card.rowId);
              return (
                <li key={card.rowId} className="grid content-start gap-2 rounded-xl border p-4 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill className={status.tone}>{status.label}</Pill>
                    <span className="text-xs text-muted-foreground">
                      {card.taskId}
                      {card.week != null && <> · Week {card.week}</>}
                    </span>
                    {waitingTasks.has(card.taskId) && (
                      <span className="text-xs font-medium text-amber-800">Answer it above</span>
                    )}
                  </div>
                  <p className="leading-snug font-medium">{card.title}</p>
                  {card.targetText && <p className="text-muted-foreground">Target: {card.targetText}</p>}

                  {card.status === "pending" && card.blockedBy.length > 0 && (
                    <p className="text-muted-foreground">Starts after {card.blockedBy.join(", ")}.</p>
                  )}

                  {answer && (
                    <p className="rounded-lg bg-muted/50 p-2.5">
                      <span className="font-medium capitalize">{answer.answer}</span>
                      {answer.at && <span className="text-muted-foreground"> · {dateTime.format(new Date(answer.at))}</span>}
                      {answer.reason && <span className="block text-muted-foreground">{answer.reason}</span>}
                    </p>
                  )}
                  {card.lastError && !answer && <p className="text-muted-foreground">{card.lastError}</p>}

                  {(card.instructions.length > 0 || card.doneWhen) && (
                    <details className="rounded-lg border">
                      <summary className="cursor-pointer px-3 py-2 font-medium">What the task involves</summary>
                      <div className="grid gap-3 border-t px-3 py-3">
                        {card.instructions.length > 0 && (
                          <ol className="grid list-decimal gap-1.5 pl-5 leading-relaxed">
                            {card.instructions.map((line, i) => (
                              <li key={i}>{line}</li>
                            ))}
                          </ol>
                        )}
                        {card.doneWhen && (
                          <p className="leading-relaxed">
                            <span className="font-medium">Done when: </span>
                            {card.doneWhen}
                          </p>
                        )}
                        {card.escalateIf && (
                          <p className="leading-relaxed">
                            <span className="font-medium">Escalate if: </span>
                            {card.escalateIf}
                          </p>
                        )}
                      </div>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
