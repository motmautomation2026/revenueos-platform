import Link from "next/link";
import { Brand } from "@/components/brand";
import { UserMenu } from "@/components/user-menu";
import { requireUser } from "@/lib/auth";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { user } = await requireUser();
  const name = (user.user_metadata?.full_name as string | undefined)?.trim() || user.email || "Account";

  return (
    <div className="flex min-h-svh flex-col bg-muted/30 print:bg-white">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b bg-background/90 backdrop-blur print:hidden">
        <div className="mx-auto flex h-14 w-full max-w-[1800px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <Link href="/projects" aria-label="RevenueOS — projects">
            <Brand />
          </Link>
          <UserMenu name={name} />
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1800px] flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        {children}
      </main>
    </div>
  );
}
