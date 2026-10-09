import Link from "next/link";
import { LogOut } from "lucide-react";
import { Brand } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { requireUser } from "@/lib/auth";
import { signOut } from "@/app/(auth)/actions";

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase() || "?";
}

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
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4">
          <Link href="/projects" aria-label="RevenueOS — projects">
            <Brand />
          </Link>
          <nav className="flex items-center gap-1">
            <Link
              href="/account"
              className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm hover:bg-muted"
              title="Account"
            >
              <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {initials(name)}
              </span>
              <span className="hidden max-w-40 truncate sm:inline">{name}</span>
            </Link>
            <form action={signOut}>
              <Button type="submit" variant="ghost" size="icon" aria-label="Sign out" title="Sign out">
                <LogOut aria-hidden />
              </Button>
            </form>
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:py-8">
        {children}
      </main>
    </div>
  );
}
