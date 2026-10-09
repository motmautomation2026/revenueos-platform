import { z } from "zod";
import { ENGAGEMENT_TYPES } from "./projects";

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

export const newProjectSchema = z.object({
  customerName: z.string().trim().min(1, "Enter the customer name").max(200),
  engagementType: z.enum(ENGAGEMENT_TYPES, "Choose an engagement type"),
  description: z.string().trim().max(2000, "Keep it under 2,000 characters").optional(),
});
export type NewProjectInput = z.infer<typeof newProjectSchema>;
