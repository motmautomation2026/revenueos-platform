import Link from "next/link";
import { ChevronDown, LogOut, UserRound } from "lucide-react";
import { signOut } from "@/app/(auth)/actions";

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase() || "?";
}

/**
 * The account menu in the header. It opens on hover, and also on focus, so it works with a
 * keyboard and on touch screens (a tap focuses the button). No script is needed.
 */
export function UserMenu({ name }: { name: string }) {
  return (
    <div className="group relative">
      <button
        type="button"
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          {initials(name)}
        </span>
        <span className="hidden max-w-40 truncate sm:inline">{name}</span>
        <ChevronDown className="size-3.5 text-muted-foreground transition-transform group-focus-within:rotate-180 group-hover:rotate-180" aria-hidden />
      </button>

      {/* The top padding bridges the gap to the button, so the menu does not close while the pointer travels to it. */}
      <div
        role="menu"
        aria-label="Account"
        className="invisible absolute top-full right-0 z-40 w-44 pt-1.5 opacity-0 transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        <div className="rounded-xl border bg-popover p-1 text-sm text-popover-foreground shadow-lg">
          <Link
            href="/account"
            role="menuitem"
            className="flex items-center gap-2 rounded-md px-2.5 py-2 outline-none hover:bg-muted focus-visible:bg-muted"
          >
            <UserRound className="size-4 text-muted-foreground" aria-hidden />
            Profile
          </Link>
          <form action={signOut}>
            <button
              type="submit"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left outline-none hover:bg-muted focus-visible:bg-muted"
            >
              <LogOut className="size-4 text-muted-foreground" aria-hidden />
              Log out
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
