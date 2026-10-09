"use server";

import { revalidatePath } from "next/cache";
import { requireUser } from "@/lib/auth";
import { profileSchema } from "@/lib/validation";
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
