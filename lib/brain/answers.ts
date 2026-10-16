// The rules for answering one of the Brain's decisions. These mirror the Brain's own code
// (brain/state_store.py ALLOWED_ANSWERS and brain/orchestrator.py NOTE_REQUIRED_KINDS) so that
// an answer given in the app is exactly what the Brain's `answer` command would have recorded.
// If the Brain adds a decision kind or an answer, add it here too: unknown kinds are refused.

export const ALLOWED_ANSWERS: Record<string, readonly string[]> = {
  plan_approval: ["yes", "no"],
  goal_check: ["continue", "more", "stop"],
  goal_check_final: ["continue", "stop"],
  // feedback = a note the email tool rewrites the samples with (brain/runner_email.py, feedback rounds)
  email_approve: ["yes", "no", "feedback"],
  call_start: ["yes", "no"],
  paid_call_unknown: ["rerun", "skip", "stop"],
  task_failed: ["retry", "skip", "stop"],
  human_task: ["done", "skip", "stop"],
  human_gate_task: ["done", "stop"],
  review_action_1: ["accept", "reject"],
  review_action_2: ["accept", "reject"],
  review_action_3: ["accept", "reject"],
};

const NOTE_REQUIRED = new Set(["human_task", "human_gate_task", "review_action_1", "review_action_2", "review_action_3"]);
export const noteRequired = (kind: string) => NOTE_REQUIRED.has(kind);

export const MAX_NOTE_LENGTH = 1000;

/** Returns a readable problem, or null when the answer may be recorded. */
export function answerProblem(kind: string, answer: string, note: string): string | null {
  const allowed = ALLOWED_ANSWERS[kind];
  if (!allowed) return "This kind of decision cannot be answered from the app.";
  if (!allowed.includes(answer)) return `Choose one of: ${allowed.join(", ")}.`;
  if (noteRequired(kind) && !note.trim()) return "Add a short note saying what was done or why.";
  if (answer === "feedback" && !note.trim()) return "Write what should change in the emails.";
  if (note.length > MAX_NOTE_LENGTH) return `Keep the note under ${MAX_NOTE_LENGTH} characters.`;
  return null;
}

export type AnswerOption = {
  answer: string;
  label: string;
  tone: "primary" | "neutral" | "danger";
  /** Shown in a confirmation step before recording; set for answers that repeat work, contact people or stop work. */
  confirm?: string;
};

const OPTIONS: Record<string, Omit<AnswerOption, "answer">> = {
  yes: { label: "Approve", tone: "primary" },
  no: { label: "Do not approve", tone: "neutral" },
  done: { label: "Mark as done", tone: "primary" },
  skip: { label: "Skip", tone: "neutral" },
  stop: { label: "Stop", tone: "danger", confirm: "This stops the task. Tasks that depend on it cannot start." },
  accept: { label: "Accept", tone: "primary" },
  reject: { label: "Reject", tone: "neutral" },
  continue: { label: "Continue as is", tone: "primary" },
  more: {
    label: "Try for more",
    tone: "neutral",
    confirm: "The Brain will run another round to get closer to the target.",
  },
  rerun: {
    label: "Run it again",
    tone: "neutral",
    confirm: "The earlier attempt may already have gone through, so running it again could do the same thing twice.",
  },
  retry: { label: "Retry", tone: "primary", confirm: "The Brain will run this task again." },
  feedback: { label: "Ask for changes", tone: "neutral" },
};

const APPROVAL_CONFIRM: Record<string, string> = {
  email_approve: "The Brain will start sending this campaign the next time it runs.",
  call_start: "The Brain will start calling these contacts the next time it runs.",
  plan_approval: "The Brain will treat this month plan as approved.",
};

const HUMAN_INTRO =
  "This is work for a person, not for the Brain. When your team has done it, write a short note and mark it done.";

/** One or two plain sentences on what a decision is and what is being asked. */
export function decisionIntro(kind: string): string {
  if (kind === "human_task" || kind === "human_gate_task") return HUMAN_INTRO;
  if (kind === "task_failed") return "A tool task stopped with an error. Nothing more happens on it until you choose.";
  if (kind === "goal_check" || kind === "goal_check_final") {
    return "The tool found fewer results than the target. Everything found so far is saved.";
  }
  if (kind === "email_approve") {
    return "Nothing is sent until you approve. Read the sample emails below; ask for changes to have them rewritten.";
  }
  if (kind === "call_start") return "No call is made until you approve. Check who will be called first.";
  if (kind === "paid_call_unknown") {
    return "The Brain was interrupted during a paid call and cannot tell whether it went through.";
  }
  if (kind.startsWith("review_action")) return "A suggestion from the Brain's review of this month. Accepting only records your choice.";
  return "";
}

/** What pressing a button does, in plain words. Shown under the buttons. */
export function answerHelp(kind: string, answer: string): string {
  const human = kind === "human_task" || kind === "human_gate_task";
  switch (answer) {
    case "done":
      return "The work is finished. Tasks waiting on it can start.";
    case "skip":
      return human
        ? "Not doing this one this month. Tasks waiting on it still go ahead."
        : "Leave this task without a result and move on.";
    case "stop":
      return "Stop this task. Tasks that depend on it will not start.";
    case "retry":
      return "Run the task again.";
    case "rerun":
      return "Run that call again. It may then happen twice.";
    case "continue":
      return "Go on with what was found.";
    case "more":
      return "Search again for more. This uses more paid credits.";
    case "yes":
      return kind === "email_approve" ? "Start sending." : kind === "call_start" ? "Start calling." : "Approve.";
    case "no":
      return "Do not go ahead. Nothing is sent or called.";
    case "feedback":
      return "The email tool rewrites the samples using your note; you then review the new version.";
    case "accept":
      return "Record that you agree.";
    case "reject":
      return "Record that you disagree.";
    default:
      return "";
  }
}

/** The buttons to offer for a decision, in the order the Brain lists its answers. */
export function answerOptions(kind: string, offered: string[]): AnswerOption[] {
  const allowed = ALLOWED_ANSWERS[kind] ?? [];
  // Positive answer first; only answers the Brain itself offered for this question.
  const order = allowed.filter((a) => !offered.length || offered.includes(a));
  return order
    .map((answer) => {
      const base = OPTIONS[answer] ?? { label: answer, tone: "neutral" as const };
      const confirm = answer === "yes" ? APPROVAL_CONFIRM[kind] : base.confirm;
      return { answer, ...base, confirm };
    })
    .sort((a, b) => Number(b.tone === "primary") - Number(a.tone === "primary"));
}

/** What happens after an answer is recorded. Nothing runs from the app itself. */
export function afterAnswer(kind: string): string {
  return kind.startsWith("review_action")
    ? "Recorded. Accepting only records the choice; nothing runs automatically."
    : "Recorded. The Brain applies it the next time it runs.";
}
