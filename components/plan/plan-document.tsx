import type { Block, Part } from "@/lib/plan/document";
import { cn } from "@/lib/utils";

// Renders the plan model as React elements. All agent text goes in as text children,
// so React escapes it; there is no dangerouslySetInnerHTML anywhere.

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "text":
      return <p className="text-sm leading-relaxed">{block.text}</p>;
    case "lead":
      return (
        <p className="border-l-2 border-primary py-0.5 pl-3 text-base leading-relaxed font-medium">
          {block.text}
        </p>
      );
    case "heading":
      return (
        <h4 className="pt-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {block.text}
        </h4>
      );
    case "list": {
      const List = block.ordered ? "ol" : "ul";
      return (
        <List
          className={cn(
            "grid gap-1 pl-5 text-sm leading-relaxed",
            block.ordered ? "list-decimal" : "list-disc"
          )}
        >
          {block.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </List>
      );
    }
    case "pairs":
      return (
        <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[minmax(9rem,14rem)_1fr]">
          {block.items.map((pair, i) => (
            <div key={i} className="contents">
              <dt className="font-medium text-muted-foreground">{pair.label}</dt>
              <dd className="leading-relaxed">{pair.value}</dd>
            </div>
          ))}
        </dl>
      );
    case "kpis":
      return (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {block.items.map((kpi, i) => (
            <div key={i} className="rounded-xl border p-3 print:break-inside-avoid">
              <p className="text-xs text-muted-foreground">{kpi.label}</p>
              <p className="mt-0.5 text-lg leading-snug font-semibold">{kpi.value}</p>
              {kpi.note && <p className="mt-1 text-[0.7rem] text-muted-foreground">{kpi.note}</p>}
            </div>
          ))}
        </div>
      );
    case "table":
      return (
        <div className="overflow-x-auto rounded-lg border print:overflow-visible">
          <table className="w-full min-w-[36rem] border-collapse text-left text-sm print:min-w-0">
            <thead className="bg-muted/60 text-xs">
              <tr>
                {block.columns.map((column, i) => (
                  <th key={i} className="border-b px-3 py-2 font-semibold">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r} className="border-b align-top last:border-0 print:break-inside-avoid">
                  {row.map((cell, c) => (
                    <td key={c} className={cn("px-3 py-2 leading-relaxed", c === 0 && "font-medium")}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "columns":
      return (
        <div
          className={cn(
            "grid gap-3 sm:grid-cols-2",
            block.columns.length === 3 && "lg:grid-cols-3",
            block.columns.length >= 4 && "xl:grid-cols-4"
          )}
        >
          {block.columns.map((column, i) => (
            <div key={i} className="rounded-xl border p-3 print:break-inside-avoid">
              <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {column.title}
              </p>
              <ul className="grid list-disc gap-1 pl-4 text-sm leading-relaxed">
                {column.items.map((item, j) => (
                  <li key={j}>{item}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      );
    case "timeline":
      return (
        <ol className="ml-1.5 grid">
          {block.phases.map((phase, i) => (
            <li key={i} className="relative border-l-2 border-primary pb-5 pl-5 last:pb-0 print:break-inside-avoid">
              <span className="absolute top-1 -left-[7px] size-3 rounded-full bg-primary" aria-hidden />
              <p className="font-semibold">{phase.title}</p>
              {phase.subtitle && <p className="text-sm text-muted-foreground">{phase.subtitle}</p>}
              {phase.activities.length > 0 && (
                <ul className="mt-2 grid list-disc gap-1 pl-4 text-sm leading-relaxed">
                  {phase.activities.map((activity, j) => (
                    <li key={j}>{activity}</li>
                  ))}
                </ul>
              )}
              {phase.milestones.length > 0 && (
                <ul className="mt-2 grid gap-1.5">
                  {phase.milestones.map((milestone, j) => (
                    <li key={j} className="rounded-lg border px-3 py-1.5 text-sm">
                      {milestone.text}
                      {milestone.value && <span className="font-semibold"> — {milestone.value}</span>}
                      {milestone.basis && (
                        <span className="text-xs text-muted-foreground"> ({milestone.basis})</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      );
  }
}

export const sectionAnchor = (part: Part, sectionId: string) => `${part.id}-${sectionId}`;

export function PlanParts({ parts }: { parts: Part[] }) {
  return (
    <div className="grid gap-12">
      {parts.map((part) => (
        <div
          key={part.id}
          className={cn(
            "grid gap-8",
            part.internal &&
              "rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 sm:p-6 print:break-before-page"
          )}
        >
          <h2 id={part.id} className="scroll-mt-20 border-b pb-2 text-xl font-semibold tracking-tight">
            {part.title}
          </h2>
          {part.sections.map((section) => (
            <section
              key={section.id}
              id={sectionAnchor(part, section.id)}
              className="grid scroll-mt-20 gap-3"
            >
              <h3 className="text-base font-semibold">{section.title}</h3>
              {section.blocks.map((block, i) => (
                <BlockView key={i} block={block} />
              ))}
            </section>
          ))}
        </div>
      ))}
    </div>
  );
}
