import { z } from "zod";

const email = z.string().trim().toLowerCase().pipe(z.email("Enter a valid email address"));
const password = z
  .string()
  .min(8, "Use at least 8 characters")
  .max(72, "Use at most 72 characters");
const fullName = z.string().trim().min(1, "Enter your full name").max(120);

export const signupSchema = z.object({ fullName, email, password });
export type SignupInput = z.infer<typeof signupSchema>;

export const loginSchema = z.object({
  email,
  password: z.string().min(1, "Enter your password"),
});
export type LoginInput = z.infer<typeof loginSchema>;

export const forgotPasswordSchema = z.object({ email });
export type ForgotPasswordInput = z.infer<typeof forgotPasswordSchema>;

export const newPasswordSchema = z
  .object({ password, confirmPassword: z.string() })
  .refine((v) => v.password === v.confirmPassword, {
    path: ["confirmPassword"],
    message: "Passwords do not match",
  });
export type NewPasswordInput = z.infer<typeof newPasswordSchema>;

export const profileSchema = z.object({ fullName });
export type ProfileInput = z.infer<typeof profileSchema>;

const optionalText = (max: number) => z.string().trim().max(max, `Keep it under ${max} characters`);

/** Company details on the profile. Every field is optional; formats are checked only when filled in. */
export const companySchema = z.object({
  companyName: optionalText(200),
  designation: optionalText(120),
  phone: optionalText(20).regex(/^$|^\+?[0-9][0-9 -]{6,18}$/, "Enter a valid phone number"),
  website: optionalText(200).regex(/^$|^(https?:\/\/)?[^\s.]+\.[^\s]+$/i, "Enter a valid website"),
  address: optionalText(300),
  city: optionalText(80),
  state: optionalText(80),
  pincode: z.string().trim().regex(/^$|^[1-9][0-9]{5}$/, "Enter a 6-digit PIN code"),
  gstin: z
    .string()
    .trim()
    .toUpperCase()
    .regex(/^$|^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/, "Enter a valid 15-character GSTIN"),
});
export type CompanyInput = z.infer<typeof companySchema>;

export const COMPANY_FIELDS = Object.keys(companySchema.shape) as (keyof CompanyInput)[];

export const newProjectSchema = z.object({
  clientName: z.string().trim().min(1, "Enter the client name").max(200),
  clientEmail: z.email("Enter the client's email").trim().toLowerCase().max(320),
  // Used to open the client's tool workspaces. Quickhowl needs at least 6 characters.
  clientPassword: z.string().min(6, "Use at least 6 characters").max(72, "Use at most 72 characters"),
  description: z.string().trim().max(2000, "Keep it under 2,000 characters").optional(),
});
export type NewProjectInput = z.infer<typeof newProjectSchema>;
