import { z } from "zod";

// Staging or Production, per project (supabase/migrations/0008_project_run_mode.sql).
//
// Staging is deliberately simple: two numbers, how many companies and how many people to fetch.
// The Brain's tools then never fetch more than that for this customer (brain/runner_kit.py,
// target_for). Nothing else changes: calls and emails go to the phone and email on each
// profile, which the person replaces with their own on the Data research page for a trial.
// The Brain's worker reads these settings from the project (brain/run_for.py, read_staging).

export type RunMode = "staging" | "production";

export type StagingSettings = {
  mode: RunMode;
  companies: number;
  people: number;
};

export const STAGING_DEFAULTS: StagingSettings = { mode: "production", companies: 5, people: 10 };
export const MAX_STAGING_COMPANIES = 100;
export const MAX_STAGING_PEOPLE = 50;

export const stagingSchema = z.object({
  mode: z.enum(["staging", "production"]),
  companies: z.coerce.number().int().min(1, "At least 1 company.").max(MAX_STAGING_COMPANIES, `At most ${MAX_STAGING_COMPANIES} companies.`),
  people: z.coerce.number().int().min(1, "At least 1 profile.").max(MAX_STAGING_PEOPLE, `At most ${MAX_STAGING_PEOPLE} profiles.`),
});
export type StagingInput = z.infer<typeof stagingSchema>;

/** What is written on the project. */
export function stagingColumns(input: StagingInput) {
  return { run_mode: input.mode, staging_companies: input.companies, staging_people: input.people };
}

/** The project's saved choice, or the defaults. `available` is false before the database update is applied. */
export function readStaging(project: {
  run_mode?: string | null;
  staging_companies?: number | null;
  staging_people?: number | null;
}): StagingSettings & { available: boolean } {
  return {
    available: project.run_mode != null,
    mode: project.run_mode === "staging" ? "staging" : "production",
    companies: project.staging_companies ?? STAGING_DEFAULTS.companies,
    people: project.staging_people ?? STAGING_DEFAULTS.people,
  };
}

// ----- Editing a profile's contact details (Data research) -----

/** An Indian mobile number as the Brain's calling step expects it: +91 and ten digits starting 6 to 9. */
export function normalisePhone(value: string): string | null {
  const digits = value.replace(/[\s\-().]/g, "");
  const match = digits.match(/^(?:\+?91|0)?([6-9]\d{9})$/);
  return match ? `+91${match[1]}` : null;
}

export const contactSchema = z.object({
  email: z
    .string()
    .trim()
    .max(320)
    .refine((value) => !value || z.email().safeParse(value).success, "Enter a valid email address."),
  phone: z
    .string()
    .trim()
    .max(30)
    .refine((value) => !value || normalisePhone(value) !== null, "Enter an Indian mobile number, e.g. +919800000001."),
});
export type ContactInput = z.infer<typeof contactSchema>;

/** The columns to write for an edited profile. An empty field clears it, so that person is not contacted that way. */
export function contactColumns(input: ContactInput) {
  return {
    email: input.email.trim().toLowerCase() || null,
    phone_number: input.phone.trim() ? normalisePhone(input.phone) : null,
  };
}
