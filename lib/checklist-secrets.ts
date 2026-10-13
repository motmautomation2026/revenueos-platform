// Some checklist answers are secrets, such as the password of the mailbox emails are sent from.
// They are stored with the checklist so they can be used, but they are never sent to the AI
// and never written into the downloadable Excel file: neither needs them.

const SECRET_QUESTION = /\b(pass\s?word|pass\s?code|app\s?password|secret\s?key|api\s?key|auth(?:entication)?\s?token)\b/i;

/** True when the question asks for a secret. Decided from the question's own wording. */
export function isSecretQuestion(question: string): boolean {
  return SECRET_QUESTION.test(question);
}

/** What stands in for a secret answer wherever it must not appear. */
export const SECRET_PROVIDED = "(provided; kept hidden)";

/** The answer as it may be shown to the AI or written to a file. */
export function answerForSharing(question: string, answer: string | null | undefined): string | null {
  if (!isSecretQuestion(question)) return answer ?? null;
  return answer?.trim() ? SECRET_PROVIDED : null;
}
