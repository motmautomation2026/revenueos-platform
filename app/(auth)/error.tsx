"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, RotateCw } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";

// Renders inside the auth layout's right column, so a crash on login / signup / reset-password shows a
// handled message instead of the default framework error page.
export default function AuthError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Auth error boundary:", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border bg-background px-6 py-10 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="size-6 text-destructive" aria-hidden />
      </span>
      <div>
        <h1 className="font-semibold">Something went wrong</h1>
        <p className="text-sm text-muted-foreground">Please try again. If it keeps happening, reload the page.</p>
      </div>
      <div className="flex gap-2">
        <Button onClick={reset}>
          <RotateCw aria-hidden />
          Try again
        </Button>
        <Link href="/login" className={buttonVariants({ variant: "outline" })}>
          Back to sign in
        </Link>
      </div>
      {error.digest && (
        <p className="text-xs text-muted-foreground">
          Reference: <code className="font-mono">{error.digest}</code>
        </p>
      )}
    </div>
  );
}
