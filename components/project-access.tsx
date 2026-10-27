"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { UserPlus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { giveAccess, revokeAccess } from "@/app/(app)/projects/[id]/access/actions";
import type { ProjectMember, ProjectRole } from "@/lib/project-access";

const ROLE_ITEMS: { value: ProjectRole; label: string }[] = [
  { value: "salesperson", label: "Salesperson — full access" },
  { value: "client", label: "Client — CRM only, view-only" },
];

const dateFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "Asia/Kolkata" });

export function ProjectAccess({ projectId, members }: { projectId: string; members: ProjectMember[] }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<ProjectRole>("salesperson");
  const [error, setError] = useState<string | null>(null);
  const [adding, startAdd] = useTransition();
  const [removingId, setRemovingId] = useState<string | null>(null);

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    startAdd(async () => {
      const result = await giveAccess(projectId, email, role);
      if (result.error) {
        setError(result.error);
        return;
      }
      toast.success(
        role === "client"
          ? `${email.trim()} can now view the CRM (read-only).`
          : `${email.trim()} now has access.`
      );
      setEmail("");
      setRole("salesperson");
      router.refresh();
    });
  };

  const remove = (member: ProjectMember) => {
    setRemovingId(member.userId);
    void (async () => {
      const result = await revokeAccess(projectId, member.userId);
      setRemovingId(null);
      if (result.error) {
        toast.error(result.error);
        return;
      }
      toast.success(`Removed ${member.name || member.email}.`);
      router.refresh();
    })();
  };

  return (
    <div className="grid gap-6">
      <div>
        <h2 className="text-lg font-semibold">Access</h2>
        <p className="text-sm text-muted-foreground">
          Give someone access by their RevenueOS email (they must already have an account). A{" "}
          <strong className="font-medium text-foreground">salesperson</strong> can open and work on this project just
          like you, including its CRM. A <strong className="font-medium text-foreground">client</strong> can only open
          the CRM for this project, and only to view — they cannot change anything.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Give access</CardTitle>
          <CardDescription>Enter the person&apos;s RevenueOS email and choose their role.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={add} className="flex flex-col gap-2 sm:flex-row" noValidate>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              autoComplete="off"
              aria-label="Email to give access to"
              className="sm:flex-1"
            />
            <Select items={ROLE_ITEMS} value={role} onValueChange={(value) => setRole(value as ProjectRole)}>
              <SelectTrigger className="sm:w-56" aria-label="Role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_ITEMS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="submit" disabled={adding || !email.trim()}>
              <UserPlus aria-hidden />
              {adding ? "Giving access…" : "Give access"}
            </Button>
          </form>
          {error && (
            <p role="alert" className="mt-2 text-sm text-destructive">
              {error}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-2">
        <h3 className="text-sm font-medium text-muted-foreground">
          People with access ({members.length})
        </h3>
        {members.length === 0 ? (
          <p className="rounded-xl border border-dashed bg-background px-6 py-8 text-center text-sm text-muted-foreground">
            Only you have access so far.
          </p>
        ) : (
          <ul className="grid gap-2">
            {members.map((member) => (
              <li key={member.userId} className="flex items-center justify-between gap-3 rounded-lg border bg-background px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium">{member.name}</p>
                    <Badge variant={member.role === "client" ? "outline" : "secondary"}>
                      {member.role === "client" ? "Client · view-only" : "Salesperson"}
                    </Badge>
                  </div>
                  <p className="truncate text-xs text-muted-foreground">
                    {member.email ? `${member.email} · ` : ""}added {dateFormat.format(new Date(member.addedAt))}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={removingId === member.userId}
                  onClick={() => remove(member)}
                  aria-label={`Remove ${member.name || member.email}`}
                >
                  <X aria-hidden />
                  {removingId === member.userId ? "Removing…" : "Remove"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
