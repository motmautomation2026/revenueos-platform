import { z } from "zod";

// The details needed to send emails as the customer: the "Email sender setup" questions.
//
// The Checklist Agent decides WHETHER to ask them (only when email campaigns are in scope).
// Their wording, order and codes are fixed here, because the Brain reads the answers by code
// (brain/run_for.py, SENDER_CODES) and the email tool accepts only certain values. A test keeps
// this file and the Brain in step.

export const SENDER_CATEGORY = "Email sender setup";

/** Shown to the person -> what the email tool is sent. It accepts only these two, on these ports. */
export const SENDER_SECURITY = {
  "STARTTLS (port 587)": { mode: "STARTTLS", port: 587 },
  "SSL/TLS (port 465)": { mode: "IMPLICIT_TLS", port: 465 },
} as const;
export const SENDER_SECURITY_OPTIONS = Object.keys(SENDER_SECURITY) as (keyof typeof SENDER_SECURITY)[];

/** Shown to the person -> the campaign type the Brain's email step is asked for. */
export const SENDER_STYLE = {
  "Personalised for each recipient": "HYPER_PERSONALIZED",
  "Same email for everyone": "STANDARD",
} as const;
export const SENDER_STYLE_OPTIONS = Object.keys(SENDER_STYLE) as (keyof typeof SENDER_STYLE)[];
export const DEFAULT_STYLE = SENDER_STYLE_OPTIONS[0];

export type SenderKey = "name" | "email" | "host" | "security" | "password" | "imapHost" | "style";

export const SENDER_ITEMS: readonly {
  key: SenderKey;
  code: string;
  question: string;
  label: string;
  hint: string;
  options: readonly string[];
  required: boolean;
}[] = [
  {
    key: "name",
    code: "SENDER-NAME",
    question: "What sender name should prospects see on your emails?",
    label: "Sender name",
    hint: "What prospects see in their inbox, e.g. Durel Sales Team.",
    options: [],
    required: true,
  },
  {
    key: "email",
    code: "SENDER-EMAIL",
    question: "Which email address should the emails be sent from?",
    label: "Sender email",
    hint: "The mailbox emails go out from. One mailbox can serve only one customer.",
    options: [],
    required: true,
  },
  {
    key: "host",
    code: "SENDER-SMTP-HOST",
    question: "What is the SMTP host of that mailbox?",
    label: "SMTP host",
    hint: "For example smtp.gmail.com or smtp.office365.com.",
    options: [],
    required: true,
  },
  {
    key: "security",
    code: "SENDER-SMTP-SECURITY",
    question: "Which SMTP security does that mailbox use?",
    label: "SMTP security",
    hint: "Most mailboxes use STARTTLS on port 587.",
    options: SENDER_SECURITY_OPTIONS,
    required: true,
  },
  {
    key: "password",
    code: "SENDER-SMTP-PASSWORD",
    question: "What is the SMTP password (or app password) of that mailbox?",
    label: "SMTP password",
    hint: "For Gmail and Outlook this is an app password, not the normal one.",
    options: [],
    required: true,
  },
  {
    key: "imapHost",
    code: "SENDER-IMAP-HOST",
    question: "What is the IMAP host of that mailbox, so replies can be read?",
    label: "IMAP host (optional)",
    hint: "Needed to count replies. Leave empty to send without reading replies.",
    options: [],
    required: false,
  },
  {
    key: "style",
    code: "SENDER-STYLE",
    question: "Should each email be personalised for its recipient, or the same for everyone?",
    label: "Email style",
    hint: "Personalised: AI rewrites each email for its recipient. Same: everyone gets one email, with their name and company filled in.",
    options: SENDER_STYLE_OPTIONS,
    required: false,
  },
];

export const SENDER_CODES = SENDER_ITEMS.map((item) => item.code);
export const SECRET_SENDER_KEYS: readonly SenderKey[] = ["password"];

const isSenderCategory = (category: string) => /email\s+sender/i.test(category);

type Question = { category: string; question: string; options: string[]; prefilled_answer?: string | null; reason?: string | null };

/**
 * The agent's questions with its own sender questions (if it asked any) replaced by the fixed
 * ones. Returns each question with the code to store it under; sender questions keep their
 * fixed code, the rest are numbered as before.
 */
export function withFixedSenderQuestions<T extends Question>(
  questions: T[]
): (Question & { code: string })[] {
  const others = questions.filter((q) => !isSenderCategory(q.category));
  const asked = others.length !== questions.length;
  const numbered = others.map((q, index) => ({ ...q, code: `Q-${index + 1}` }));
  if (!asked) return numbered;
  return [
    ...numbered,
    ...SENDER_ITEMS.map((item) => ({
      code: item.code,
      category: SENDER_CATEGORY,
      question: item.question,
      options: [...item.options],
      prefilled_answer: null,
      reason: "Needed to send emails from the customer's own mailbox.",
    })),
  ];
}

const HOST = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;
const line = (max: number) => z.string().trim().max(max, `Keep it under ${max} characters.`);
const host = line(253).refine((value) => !value || HOST.test(value), "Enter a host name such as smtp.gmail.com.");
const email = line(320).refine((value) => !value || z.email().safeParse(value).success, "Enter a valid email.");

/** The Sender account form. Everything may be left empty; an empty password keeps the saved one. */
export const senderSchema = z.object({
  loginEmail: email,
  name: line(200),
  email,
  host,
  security: z.union([z.enum(SENDER_SECURITY_OPTIONS), z.literal("")]),
  password: z.string().max(500, "Keep it under 500 characters."),
  imapHost: host,
  style: z.union([z.enum(SENDER_STYLE_OPTIONS), z.literal("")]),
});
export type SenderInput = z.infer<typeof senderSchema>;

export type SenderView = {
  /** The customer's email: the login of their email-tool account. */
  loginEmail: string;
  values: Record<SenderKey, string>;
  passwordSaved: boolean;
  /** Labels of what is still needed before the first campaign can go out. */
  missing: string[];
  /** A different account already exists for this customer (set under Tool accounts). */
  existingAccount: boolean;
};

/** Sender answers by code -> what the page shows. The password itself never leaves the server. */
export function buildSenderView(
  answers: Record<string, string | null | undefined>,
  loginEmail: string | null | undefined,
  existingAccount = false
): SenderView {
  const values = {} as Record<SenderKey, string>;
  const missing: string[] = [];
  for (const item of SENDER_ITEMS) {
    const value = (answers[item.code] ?? "").trim();
    values[item.key] = SECRET_SENDER_KEYS.includes(item.key) ? "" : value;
    if (item.required && !value) missing.push(item.label);
  }
  if (values.security && !(values.security in SENDER_SECURITY)) {
    // An older checklist may hold "SSL" or "TLS": the email tool takes neither.
    values.security = "";
    missing.push("SMTP security");
  }
  if (!(values.style in SENDER_STYLE)) values.style = DEFAULT_STYLE;
  const login = (loginEmail ?? "").trim();
  if (!login) missing.unshift("Customer email");
  return {
    loginEmail: login,
    values,
    passwordSaved: !!(answers["SENDER-SMTP-PASSWORD"] ?? "").trim(),
    missing: existingAccount ? [] : [...new Set(missing)],
    existingAccount,
  };
}

/** The checklist answers to write for a submitted form, by code. An empty password is left out. */
export function senderAnswers(input: SenderInput): Record<string, string | null> {
  const answers: Record<string, string | null> = {};
  for (const item of SENDER_ITEMS) {
    const value = input[item.key];
    if (SECRET_SENDER_KEYS.includes(item.key)) {
      if (value) answers[item.code] = value;
    } else {
      answers[item.code] = value.trim() || null;
    }
  }
  return answers;
}
