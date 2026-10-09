import type { LucideIcon } from "lucide-react";

export function StepPlaceholder({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
      <Icon className="size-8 text-muted-foreground" aria-hidden />
      <div className="max-w-md">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{children}</p>
      </div>
    </div>
  );
}
