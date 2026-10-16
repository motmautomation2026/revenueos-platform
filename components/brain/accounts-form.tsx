"use client";

import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Mail, Phone, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { ACCOUNT_FIELDS, accountsSchema, type AccountsInput, type AccountsView } from "@/lib/brain/accounts";
import { saveToolAccounts } from "@/app/(app)/projects/[id]/execution/actions";

const TOOLS = [
  { key: "email", title: "Email", tool: "Email tool", icon: Mail },
  { key: "calls", title: "Calls", tool: "Quickhowl", icon: Phone },
] as const;

export function AccountsForm({ projectId, view }: { projectId: string; view: AccountsView }) {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<AccountsInput>({ resolver: zodResolver(accountsSchema), defaultValues: view.values });

  const onSubmit = handleSubmit(async (input) => {
    const result = await saveToolAccounts({ projectId, accounts: input });
    if (!result.ok) {
      toast.error(result.error);
      return;
    }
    toast.success("Tool accounts saved.");
    reset({ ...input, calls_user_password: "" }); // the password is not kept in the browser
    router.refresh();
  });

  return (
    <form onSubmit={onSubmit} className="grid gap-6" noValidate>
      {view.missing.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-emerald-800">
          <CheckCircle2 className="size-4" aria-hidden />
          Nothing more is needed here.
        </p>
      ) : (
        <div className="grid gap-1 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
          <p className="flex items-center gap-2 font-semibold text-amber-900">
            <TriangleAlert className="size-4" aria-hidden />
            Execution cannot start until these are filled in
          </p>
          <p className="text-amber-900">{view.missing.join(" · ")}</p>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        {TOOLS.map(({ key, title, tool, icon: Icon }) => (
          <fieldset key={key} className="grid content-start gap-4 rounded-xl border p-5">
            <legend className="sr-only">{title}</legend>
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="size-4" aria-hidden />
              </span>
              <div>
                <p className="font-semibold">{title}</p>
                <p className="text-xs text-muted-foreground">{tool}</p>
              </div>
            </div>
            {ACCOUNT_FIELDS.filter((field) => field.tool === key).map((field) => {
              const secret = "secret" in field && field.secret;
              return (
                <FormField
                  key={field.column}
                  id={field.column}
                  label={field.label}
                  error={errors[field.column]?.message}
                  hint={secret && view.saved[field.column] ? "Saved. Leave empty to keep it, or type a new one." : field.hint}
                >
                  <Input
                    id={field.column}
                    type={secret ? "password" : "type" in field ? field.type : "text"}
                    autoComplete={secret ? "new-password" : "off"}
                    placeholder={secret && view.saved[field.column] ? "••••••••" : undefined}
                    {...register(field.column)}
                  />
                </FormField>
              );
            })}
          </fieldset>
        ))}
      </div>

      <Button type="submit" disabled={isSubmitting || !isDirty} className="justify-self-start">
        {isSubmitting ? "Saving…" : "Save tool accounts"}
      </Button>
    </form>
  );
}
