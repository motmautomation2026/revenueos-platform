import { cn } from "@/lib/utils";

/** The RevenueOS wordmark: a rising-bars mark plus the name. `inverted` is for dark backgrounds. */
export function Brand({
  className,
  size = "md",
  inverted = false,
}: {
  className?: string;
  size?: "md" | "lg";
  inverted?: boolean;
}) {
  const bar = inverted ? "bg-primary" : "bg-primary-foreground";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 font-semibold tracking-tight",
        size === "lg" ? "text-xl" : "text-base",
        className
      )}
    >
      <span
        aria-hidden
        className={cn(
          "flex items-end justify-center gap-[3px] rounded-lg",
          inverted ? "bg-primary-foreground" : "bg-primary",
          size === "lg" ? "size-9 p-2" : "size-7 p-1.5"
        )}
      >
        <span className={cn("h-[40%] w-full rounded-sm opacity-60", bar)} />
        <span className={cn("h-[70%] w-full rounded-sm opacity-80", bar)} />
        <span className={cn("h-full w-full rounded-sm", bar)} />
      </span>
      <span>
        Revenue<span className={inverted ? "opacity-80" : "text-primary"}>OS</span>
      </span>
    </span>
  );
}
