import { FileUp, ListChecks, Route } from "lucide-react";
import { Brand } from "@/components/brand";

const STEPS = [
  { icon: FileUp, title: "Upload the customer's documents", text: "Handover, proposal, signed scope and notes." },
  { icon: ListChecks, title: "Fill in a tailored checklist", text: "40–50 questions, with known answers prefilled." },
  { icon: Route, title: "Get the go-to-market plan", text: "Diagnosis, strategy and execution in one document." },
];

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-svh lg:grid-cols-2">
      <section className="hidden flex-col justify-between bg-primary p-10 text-primary-foreground lg:flex">
        <Brand size="lg" inverted />
        <div className="grid max-w-md gap-8">
          <h1 className="text-3xl leading-tight font-semibold tracking-tight">
            From signed customer to go-to-market plan.
          </h1>
          <ol className="grid gap-5">
            {STEPS.map((step) => (
              <li key={step.title} className="flex gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-foreground/15">
                  <step.icon className="size-4.5" aria-hidden />
                </span>
                <div>
                  <p className="font-medium">{step.title}</p>
                  <p className="text-sm text-primary-foreground/75">{step.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <p className="text-sm text-primary-foreground/70">MOTM Technologies</p>
      </section>

      <section className="flex flex-col items-center justify-center bg-muted/40 px-4 py-10">
        <Brand size="lg" className="mb-6 lg:hidden" />
        <div className="w-full max-w-sm">{children}</div>
      </section>
    </main>
  );
}
