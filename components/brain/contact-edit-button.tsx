"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Pencil } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { contactSchema } from "@/lib/brain/staging";
import { updateContact } from "@/app/(app)/projects/[id]/execution/actions";

/** Change the email and phone on one profile. Calls and emails go to whatever is saved here. */
export function ContactEditButton({
  projectId,
  personId,
  name,
  email,
  phone,
}: {
  projectId: string;
  personId: number;
  name: string;
  email: string;
  phone: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState({ email, phone });
  const [errors, setErrors] = useState<{ email?: string; phone?: string }>({});
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const parsed = contactSchema.safeParse(value);
    if (!parsed.success) {
      const found: { email?: string; phone?: string } = {};
      for (const issue of parsed.error.issues) found[issue.path[0] as "email" | "phone"] = issue.message;
      setErrors(found);
      return;
    }
    setBusy(true);
    try {
      const result = await updateContact({ projectId, personId, contact: value });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(`${name || "Profile"} updated.`);
      setOpen(false);
      router.refresh();
    } catch {
      toast.error("That could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        aria-label={`Edit email and phone of ${name || "this profile"}`}
        onClick={() => {
          setValue({ email, phone });
          setErrors({});
          setOpen(true);
        }}
      >
        <Pencil aria-hidden />
        Edit
      </Button>

      <AlertDialog open={open} onOpenChange={(next) => !busy && setOpen(next)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{name || "Profile"}</AlertDialogTitle>
            <AlertDialogDescription>
              Calls and emails for this profile go to the phone and email saved here. Leave one empty and this
              profile is not contacted that way.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="grid gap-4">
            <FormField id={`contact-email-${personId}`} label="Email" error={errors.email}>
              <Input
                id={`contact-email-${personId}`}
                type="email"
                autoComplete="off"
                value={value.email}
                disabled={busy}
                onChange={(e) => {
                  setValue((current) => ({ ...current, email: e.target.value }));
                  setErrors((current) => ({ ...current, email: undefined }));
                }}
              />
            </FormField>
            <FormField id={`contact-phone-${personId}`} label="Phone" error={errors.phone} hint="An Indian mobile number.">
              <Input
                id={`contact-phone-${personId}`}
                type="tel"
                autoComplete="off"
                placeholder="+919800000001"
                value={value.phone}
                disabled={busy}
                onChange={(e) => {
                  setValue((current) => ({ ...current, phone: e.target.value }));
                  setErrors((current) => ({ ...current, phone: undefined }));
                }}
              />
            </FormField>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              Save
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
