import Link from "next/link";
import { Brand } from "@/components/brand";
import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-5 bg-muted/30 px-4 text-center">
      <Brand size="lg" />
      <div>
        <h1 className="text-xl font-semibold">Page not found</h1>
        <p className="text-sm text-muted-foreground">
          This page does not exist, or it belongs to a project you cannot open.
        </p>
      </div>
      <Link href="/projects" className={buttonVariants()}>
        Go to projects
      </Link>
    </main>
  );
}
