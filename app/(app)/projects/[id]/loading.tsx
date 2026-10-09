import { Skeleton } from "@/components/ui/skeleton";

/** Shown inside the project header and stepper while a step's data loads. */
export default function StepLoading() {
  return (
    <div className="grid gap-4" aria-busy aria-label="Loading">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <Skeleton className="h-40 rounded-xl" />
      <Skeleton className="h-16 rounded-xl" />
      <Skeleton className="h-16 rounded-xl" />
    </div>
  );
}
