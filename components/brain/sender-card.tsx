"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Loader2, Mail, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import {
  SENDER_ITEMS,
  SENDER_SECURITY_OPTIONS,
  SENDER_STYLE_OPTIONS,
  makeSenderSchema,
  type SenderInput,
  type SenderView,
} from "@/lib/checklist-sender";
import { chooseEmailAccount, saveSenderAccount, testSenderEmail } from "@/app/(app)/projects/[id]/execution/actions";
import { ToolTest } from "@/components/brain/tool-test";
import { cn } from "@/lib/utils";

const CHOICES = [
  {
    mode: "own" as const,
    title: "Give this customer its own email account",
    note: "Emails go out from this customer's own mailbox. Enter its sending details below.",
  },
  {
    mode: "existing" as const,
    title: "Use an existing email account",
    note: "Emails go out from another project's account and connected mailbox. Quick, for a trial.",
  },
];

/**
 * The customer's sending account: who emails come from. Either its OWN account (the sender details,
 * from which the Brain opens a new account on the first campaign) or an EXISTING account reused from
 * another of the user's projects (its already-connected mailbox is used, so no sender details needed).
 */
export function SenderCard({ projectId, view }: { projectId: string; view: SenderView }) {
  const router = useRouter();
  const ready = view.missing.length === 0;
  const [editing, setEditing] = useState(!ready && !view.existingAccount);
  const [mode, setMode] = useState<"own" | "existing">(view.existingAccount ? "existing" : "own");
  const [sourceProjectId, setSourceProjectId] = useState(
    view.options.find((option) => option.reference === view.existingReference)?.projectId ?? view.options[0]?.projectId ?? ""
  );
  const [busy, setBusy] = useState(false);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<SenderInput>({
    // The password is required only on first setup; once saved, an empty field keeps it.
    resolver: zodResolver(makeSenderSchema(!view.passwordSaved)),
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

  // Reuse another project's account, or switch back to this project's own.
  const applyAccount = async (next: "own" | "existing") => {
    setBusy(true);
    try {
      const result = await chooseEmailAccount({
        projectId,
        mode: next,
        sourceProjectId: next === "existing" ? sourceProjectId : undefined,
      });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(result.message);
      setEditing(false);
      router.refresh();
    } catch {
      toast.error("That could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const accountLabel = (reference: string, email: string) => (email ? `${reference} · ${email}` : reference);

  return (
    <section aria-label="Sender account" className="grid gap-4 rounded-xl border p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Mail className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold">Sender account</p>
          {view.existingAccount ? (
            <p className="flex flex-wrap items-center gap-x-2 text-sm">
              <CheckCircle2 className="size-4 text-emerald-700" aria-hidden />
              <span className="font-medium">{view.existingReference || "An existing email account"}</span>
              {view.existingEmail && <span className="text-muted-foreground">· {view.existingEmail}</span>}
              <span className="text-muted-foreground">· reused account, its connected mailbox sends</span>
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
        {!editing && (
          <Button
            variant="outline"
            onClick={() => {
              setMode(view.existingAccount ? "existing" : "own");
              setEditing(true);
            }}
          >
            {view.existingAccount ? "Change" : ready ? "Update sender details" : "Add sender details"}
          </Button>
        )}
      </div>

      {ready && !editing && !view.existingAccount && (
        <>
          <p className="text-xs text-muted-foreground">
            The account is created in the email tool with this customer&apos;s first campaign. If the mailbox cannot be
            connected, that campaign stops and says why; nothing is sent.
          </p>
          <ToolTest
            title="Test the sender"
            hint="Send a test email to your own inbox, straight from this mailbox, to check it arrives."
            inputLabel="Your email address"
            placeholder="you@example.com"
            buttonLabel="Send a test email"
            inputType="email"
            run={(target) => testSenderEmail({ projectId, target })}
          />
        </>
      )}

      {editing && (
        <div className="grid gap-4 border-t pt-4">
          <div role="radiogroup" aria-label="Email account" className="grid gap-3 md:grid-cols-2">
            {CHOICES.map((choice) => {
              const disabled = choice.mode === "existing" && view.options.length === 0;
              return (
                <button
                  key={choice.mode}
                  type="button"
                  role="radio"
                  aria-checked={mode === choice.mode}
                  disabled={busy || isSubmitting || disabled}
                  onClick={() => setMode(choice.mode)}
                  className={cn(
                    "grid gap-1 rounded-lg border p-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                    mode === choice.mode ? "border-primary bg-primary/5" : "hover:bg-muted"
                  )}
                >
                  <span className="text-sm font-semibold">{choice.title}</span>
                  <span className="text-sm text-muted-foreground">
                    {disabled ? "No other project has an email account yet." : choice.note}
                  </span>
                </button>
              );
            })}
          </div>

          {mode === "existing" ? (
            <>
              <FormField
                id="email-source"
                label="Use the email account of"
                hint="Emails go out from that account's connected mailbox. No sending details are needed here."
              >
                <select
                  id="email-source"
                  value={sourceProjectId}
                  disabled={busy}
                  onChange={(e) => setSourceProjectId(e.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 sm:max-w-sm"
                >
                  {view.options.map((option) => (
                    <option key={option.projectId} value={option.projectId}>
                      {accountLabel(option.reference, option.email)}
                    </option>
                  ))}
                </select>
              </FormField>
              <div className="flex gap-2">
                <Button disabled={busy || !sourceProjectId} onClick={() => void applyAccount("existing")}>
                  {busy && <Loader2 className="animate-spin" aria-hidden />}
                  Use this account
                </Button>
                {(ready || view.existingAccount) && (
                  <Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </>
          ) : (
            <form onSubmit={onSubmit} className="grid gap-4" noValidate>
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
                {view.existingAccount && (
                  <Button type="button" variant="outline" disabled={busy} onClick={() => void applyAccount("own")}>
                    {busy && <Loader2 className="animate-spin" aria-hidden />}
                    Stop reusing, use own
                  </Button>
                )}
                {(ready || view.existingAccount) && (
                  <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </form>
          )}
        </div>
      )}
    </section>
  );
}
