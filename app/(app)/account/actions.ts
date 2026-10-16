"use server";

import { revalidatePath } from "next/cache";
import { requireUser } from "@/lib/auth";
import { companySchema, profileSchema } from "@/lib/validation";
import { TABLES } from "@/lib/supabase/tables";

export async function updateProfile(
  input: unknown
): Promise<{ ok: true } | { ok: false; error: string }> {
  const { supabase, user } = await requireUser();

  const parsed = profileSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Enter your full name." };
  const { fullName } = parsed.data;

  const { error } = await supabase
    .from(TABLES.profiles)
    .update({ full_name: fullName })
    .eq("id", user.id);
  if (error) return { ok: false, error: "Could not save your name. Please try again." };

  await supabase.auth.updateUser({ data: { full_name: fullName } });
  revalidatePath("/account");
  return { ok: true };
}

/**
 * Company details are kept on the user's own account record (auth metadata), so they need no
 * extra table and are only ever readable and writable by that user.
 */
export async function updateCompany(
  input: unknown
): Promise<{ ok: true } | { ok: false; error: string }> {
  const { supabase } = await requireUser();

  const parsed = companySchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false, error: parsed.error.issues[0]?.message ?? "Please check the form and try again." };
  }

  const { error } = await supabase.auth.updateUser({ data: { company: parsed.data } });
  if (error) return { ok: false, error: "Could not save the company details. Please try again." };

  revalidatePath("/account");
  return { ok: true };
}
