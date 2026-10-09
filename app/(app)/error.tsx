"use client";

import Link from "next/link";
import { AlertTriangle, RotateCw } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";

export default function AppError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 rounded-xl border bg-background px-6 py-14 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="size-6 text-destructive" aria-hidden />
      </span>
      <div>
        <h1 className="font-semibold">Something went wrong on this page</h1>
        <p className="text-sm text-muted-foreground">
          Your saved work is not affected. Try again, or go back to your projects.
        </p>
      </div>
      <div className="flex gap-2">
        <Button onClick={reset}>
          <RotateCw aria-hidden />
          Try again
        </Button>
        <Link href="/projects" className={buttonVariants({ variant: "outline" })}>
          Projects
        </Link>
      </div>
    </div>
  );
}
