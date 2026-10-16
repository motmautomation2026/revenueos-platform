"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Mail, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import {
  SENDER_ITEMS,
  SENDER_SECURITY_OPTIONS,
  SENDER_STYLE_OPTIONS,
  senderSchema,
  type SenderInput,
  type SenderView,
} from "@/lib/checklist-sender";
import { saveSenderAccount } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * The customer's own sending account: who emails come from. Filled in once, before the first
 * campaign; the Brain creates the account in the email tool with that campaign.
 */
export function SenderCard({ projectId, view }: { projectId: string; view: SenderView }) {
  const router = useRouter();
  const ready = view.missing.length === 0;
  const [editing, setEditing] = useState(!ready && !view.existingAccount);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<SenderInput>({
    resolver: zodResolver(senderSchema),
    defaultValues: {
      loginEmail: view.loginEmail,
      ...view.values,
      security: view.values.security as SenderInput["security"],
      style: view.values.style as SenderInput["style"],
    },
  });

  const onSubmit = handleSubmit(async (input) => {
    const result = await saveSenderAccount({ projectId, sender: input });
    if (!result.ok) {
      toast.error(result.error);
      return;
    }
    toast.success("Sender details saved.");
    reset({ ...input, password: "" }); // the password is not kept in the browser
    setEditing(false);
    router.refresh();
  });

  return (
    <section aria-label="Sender account" className="grid gap-4 rounded-xl border p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Mail className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold">Sender account</p>
          {view.existingAccount ? (
            <p className="text-sm text-muted-foreground">
              This customer already has an account in the email tool. Campaigns go out from its connected mailbox.
            </p>
          ) : ready ? (
            <p className="flex flex-wrap items-center gap-x-2 text-sm">
              <CheckCircle2 className="size-4 text-emerald-700" aria-hidden />
              <span className="font-medium">{view.values.email}</span>
              <span className="text-muted-foreground">
                · “{view.values.name}” · account login {view.loginEmail} · {view.values.style.toLowerCase()}
              </span>
            </p>
          ) : (
            <p className="flex flex-wrap items-center gap-x-2 text-sm text-amber-900">
              <TriangleAlert className="size-4" aria-hidden />
              Emails cannot go out yet. Still needed: {view.missing.join(", ")}.
            </p>
          )}
        </div>
        {!view.existingAccount && !editing && (
          <Button variant="outline" onClick={() => setEditing(true)}>
            {ready ? "Update sender details" : "Add sender details"}
          </Button>
        )}
      </div>

      {ready && !editing && !view.existingAccount && (
        <p className="text-xs text-muted-foreground">
          The account is created in the email tool with this customer&apos;s first campaign. If the mailbox cannot be
          connected, that campaign stops and says why; nothing is sent.
        </p>
      )}

      {editing && (
        <form onSubmit={onSubmit} className="grid gap-4 border-t pt-4" noValidate>
          <FormField
            id="loginEmail"
            label="Customer email"
            error={errors.loginEmail?.message}
            hint="This customer's email and calling accounts are opened with it. It cannot be changed after the first campaign."
          >
            <Input id="loginEmail" type="email" autoComplete="off" className="sm:max-w-md" {...register("loginEmail")} />
          </FormField>

          <div className="grid gap-4 md:grid-cols-2">
            {SENDER_ITEMS.map((item) => {
              const secret = item.key === "password";
              return (
                <FormField
                  key={item.key}
                  id={`sender-${item.key}`}
                  label={item.label}
                  error={errors[item.key]?.message}
                  hint={secret && view.passwordSaved ? "Saved. Leave empty to keep it, or type a new one." : item.hint}
                >
                  {item.key === "security" || item.key === "style" ? (
                    <select
                      id={`sender-${item.key}`}
                      className="h-9 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                      {...register(item.key)}
                    >
                      {item.key === "security" && <option value="">Choose…</option>}
                      {(item.key === "security" ? SENDER_SECURITY_OPTIONS : SENDER_STYLE_OPTIONS).map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      id={`sender-${item.key}`}
                      type={secret ? "password" : item.key === "email" ? "email" : "text"}
                      autoComplete={secret ? "new-password" : "off"}
                      placeholder={secret && view.passwordSaved ? "••••••••" : undefined}
                      {...register(item.key)}
                    />
                  )}
                </FormField>
              );
            })}
          </div>

          <div className="flex gap-2">
            <Button type="submit" disabled={isSubmitting || !isDirty}>
              {isSubmitting ? "Saving…" : "Save sender details"}
            </Button>
            {(ready || view.existingAccount) && (
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
