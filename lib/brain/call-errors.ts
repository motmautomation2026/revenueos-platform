// Why a call campaign stopped, in plain words. The Brain and Quickhowl report problems as
// technical text (HTTP codes, field names); the Calls page shows what happened and what to do,
// and keeps the original under "Technical details".

export type CallProblem = { what: string; next: string };

const KNOWN: { match: RegExp; problem: CallProblem }[] = [
  {
    match: /no (usable|connected) phone number/i,
    problem: {
      what: "This customer's calling account has no phone number to call from.",
      next: "Choose a calling account above, then press Retry on the Dashboard.",
    },
  },
  {
    match: /different user\.?email|belongs to an account with a different/i,
    problem: {
      what: "The campaign was sent to a calling account that belongs to a different user.",
      next: "Press Retry on the Dashboard: it now goes to the calling account chosen above.",
    },
  },
  {
    match: /reference reused with different content/i,
    problem: {
      what: "Quickhowl already has a campaign under this name with different content.",
      next: "Press Retry on the Dashboard. If it happens again, stop this task and plan the calls again.",
    },
  },
  {
    match: /internal words/i,
    problem: {
      what: "The AI's call script used internal wording a prospect must not hear, so it was not sent.",
      next: "Press Retry on the Dashboard: the script is written again.",
    },
  },
  {
    match: /no MOBILE number|No contacts with a valid phone/i,
    problem: {
      what: "None of the contacts has a mobile number that can be called.",
      next: "Add or fix phone numbers under Data research, then press Retry on the Dashboard.",
    },
  },
  {
    match: /Real calls are off|DND/i,
    problem: {
      what: "Real calls are switched off in the Brain's settings.",
      next: "This is a setting on the Brain's machine; ask whoever runs it to switch real calls on.",
    },
  },
  {
    match: /still a DRAFT after start/i,
    problem: {
      what: "The campaign was approved but Quickhowl did not start it.",
      next: "Check the campaign in Quickhowl, then press Retry on the Dashboard.",
    },
  },
  {
    match: /did not answer|timed out|timeout|ConnectError|getaddrinfo/i,
    problem: {
      what: "Quickhowl could not be reached.",
      next: "Check the internet connection on the Brain's machine, then press Retry on the Dashboard.",
    },
  },
  {
    match: /\b401\b|Invalid .*key|unauthenticated/i,
    problem: {
      what: "Quickhowl refused the Brain's access key.",
      next: "The key in the Brain's settings is wrong or expired; it has to be corrected there.",
    },
  },
];

/** What happened and what to do, or a short generic line for a problem that is not recognised. */
export function explainCallProblem(raw: string | null | undefined): CallProblem | null {
  const text = (raw ?? "").trim();
  if (!text) return null;
  return (
    KNOWN.find((known) => known.match.test(text))?.problem ?? {
      what: "The call campaign stopped with an error.",
      next: "Open Technical details below for the exact message, then press Retry on the Dashboard.",
    }
  );
}
