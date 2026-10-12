"""
brain_review.py
===============

The brain reviews a run's results against the plan: 'python orchestrator.py review <YYYY-MM>'.
Nothing runs automatically from a review.

WHO DECIDES WHAT
----------------
    Code : EVERY number (achieved, targets, % of target, coverage, outcomes, objection counts,
           data-quality %), which signals are flagged (thresholds), which tasks are reviewed, and
           whether the LLM's text is acceptable (only numbers it was given; real plan rules)
    LLM  : one label per call (from a FIXED list) + a short quote; the explanation (what worked,
           what is short) and at most 3 suggested next actions
    Human: accept / reject each suggested action (with a note). Accepting only records the choice.

FLOW
----
    1. numbers     from the run's DONE tool tasks (their saved results) and the task statuses:
                   T3 accounts  achieved / effective target / plan target, reused, bought
                   T4 contacts  + with email / with phone / mobile / landline
                   T11 email    recipients, rejected, campaign status; opens / replies: NOT AVAILABLE YET
                   T7 calls     completed, outcome counts, per-call outcome + summary (max 10), skipped landlines,
                                contacts Quickhowl rejected (total + do-not-call; no threshold flag)
                   TEST_MODE: the effective target is the test cap, the plan target the real one.
    2. data        code, from the ledger: % landline (T4 phones), % without email (T4 people),
       quality     % failed the judge (companies T3, people T4: reuse + bought rounds).
                   FLAGGED (passed to the LLM as 'fix this') when >= THRESHOLDS.
    3. objections  every call's summary + transcript (prospect lines first, max 1500 chars) from the
                   ledger. LLM labels each call with ONE of OBJECTION_LABELS + a quote, 20 calls per LLM
                   call; code counts the labels (an unknown label -> 'other').
    4. text        1 LLM call: numbers + data quality + objections + plan goals -> what_worked,
                   whats_short, next_actions (max 3). Each action has a kind and 'follows_rule':
                   one of the plan's optimization rules, copied exactly, or 'new'.
                   Code checks: numbers only from the data (0-3 always allowed), real plan rules, and no
                   claimed EVENT the RESULTS do not show (CLAIMS: demo/meeting booked, deal won; e.g.
                   'demo scheduled' when the outcome is only 'interested'; negated sentences are fine).
                   Fails -> ONE retry naming the problem. Numbers or claims still wrong -> numbers-only review.
                   Rules still not matched -> 'new (rule not matched)' + a note.
                   TEST_MODE: code DROPS every 'volume' action and says once: volume shortfalls are test caps.
    5. saved       each action -> an OPEN decision review_action_1..3 (accept / reject + note), run-level;
                   the review -> an answered decision 'run_review' (decided_by brain); each reviewed
                   task's result gets a 'review' entry (runner fields are never changed).
    A new review is refused while earlier review actions are still open (answer them first).

COST
----
    LLM calls only: ceil(calls / 20) for labelling (0 if no calls) + 1 review (+1 if the check fails).
    Today (1 call): 2, at most 3. No QuickICP, email tool or Quickhowl call.

DEBUGGING
---------
    "STOPPED: answer the open review actions first"  -> python orchestrator.py decisions
    "explanation rejected: invented numbers [...]"    -> the LLM used numbers not in the data twice
    "explanation rejected: claims not in the data"    -> the LLM claimed e.g. a booked demo twice; RESULTS
                                                         had no such outcome (see CLAIMS)
    "follows_rule not matched"                        -> the LLM named a rule that is not in the plan
    The saved review: brain_decisions where kind = 'run_review' (question.numbers / question.explanation).
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from quickicp import ToolError, ask_llm
from state_store import (get_steps, get_tasks, list_open_decisions, now_iso, open_decision, ready_tasks,
                         record_decision, update_task)

REVIEW_ACTION_KINDS = ("review_action_1", "review_action_2", "review_action_3")
MAX_ACTIONS = 3
MAX_CALL_SUMMARIES = 10
NOT_AVAILABLE = "not available yet (developer results endpoint pending)"
FREE_NUMBERS = {"0", "1", "2", "3"}          # small counts / ordinals the text may always use
TOOL_TASKS = {("QuickICP", "accounts"), ("QuickICP", "contacts"), ("Email Agent", "emails"), ("Quickhowl", "calls")}
TEST_CAP_LINE = "Volume shortfalls are test caps (TEST_MODE), not a problem to fix."

# Data-quality signals: flagged (the LLM must suggest a fix) at or above these percentages.
THRESHOLDS = {"landline_pct": 20, "without_email_pct": 20,
              "companies_failed_judge_pct": 30, "people_failed_judge_pct": 30}

# Call objections: the LLM picks ONE of these per call; code counts them.
OBJECTION_LABELS = ("not_interested", "building_own_solution", "already_has_vendor", "call_later", "wrong_person",
                    "budget_or_price", "no_need_now", "busy_or_bad_time", "language_or_audio", "interested", "other")
LABEL_BATCH = 20
TRANSCRIPT_CHARS = 1500

ActionKind = Literal["quality", "script", "targeting", "data_quality", "process", "volume"]


# ================= WHAT THE LLM RETURNS =================

class NextAction(BaseModel):
    action: str = Field(description="One concrete next step for the MOTM team, one sentence")
    why: str = Field(description="Why, based ONLY on the numbers, objections and plan given")
    task_id: str = Field(description="The plan task it concerns, e.g. 'T7', or 'run' for the whole run")
    kind: ActionKind = Field(default="quality", description="quality, script, targeting, data_quality, process or volume")
    follows_rule: str = Field(default="new", description="ONE plan optimization rule copied EXACTLY, or 'new'")


class ReviewText(BaseModel):
    what_worked: list[str] = Field(description="At most 3 short points")
    whats_short: list[str] = Field(description="At most 3 short points, against the plan's targets and KPIs")
    next_actions: list[NextAction] = Field(description=f"At most {MAX_ACTIONS} suggested actions")


class CallLabel(BaseModel):
    index: int = Field(description="The call's index exactly as given")
    label: str = Field(description="Exactly one label from the allowed list")
    quote: str = Field(description="The prospect's own words that show it, max 15 words")


class CallLabels(BaseModel):
    labels: list[CallLabel]


# ================= NUMBERS (code only) =================

def pct(part, whole) -> int | None:
    """Whole-number percentage, or None if it cannot be computed."""
    return round(100 * part / whole) if isinstance(part, (int, float)) and whole else None


def task_numbers(task: dict) -> dict:
    """Numbers of one DONE tool task, from its saved result."""
    r = task["result"] or {}
    achieved, effective, plan = task["achieved_number"], r.get("target"), task["target_number"]
    entry = {"task_id": task["task_id"], "agent": task["agent"], "output_type": task["output_type"],
             "plan_target": task["work_order"].get("target"), "plan_target_number": plan,
             "effective_target": effective, "achieved": achieved,
             "pct_of_effective_target": pct(achieved, effective), "pct_of_plan_target": pct(achieved, plan)}
    kind = task["output_type"]
    if kind == "accounts":
        entry.update(reused=r.get("reused"), bought=r.get("bought"))
    elif kind == "contacts":
        entry.update({k: r.get(k) for k in ("reused", "bought", "with_email", "with_phone", "mobile", "landline")})
    elif kind == "emails":
        entry.update(recipients=r.get("recipients"), rejected=r.get("rejected"),
                     campaign_status=r.get("campaign_status"), email_opens=NOT_AVAILABLE, email_replies=NOT_AVAILABLE)
    elif kind == "calls":
        calls = sorted(r.get("outcomes") or [], key=lambda c: str(c.get("name")))[:MAX_CALL_SUMMARIES]
        entry.update(calls_completed=achieved, outcome_counts=r.get("outcome_counts"),
                     skipped_landlines=len(r.get("skipped_landlines") or []),
                     rejected_by_quickhowl=r.get("rejected_by_quickhowl", 0),
                     rejected_do_not_call=r.get("rejected_do_not_call", 0),
                     calls=[{"name": c.get("name"), "company": c.get("company"), "outcome": c.get("outcome"),
                             "duration_seconds": c.get("duration_seconds"),
                             "summary": (c.get("summary") or "")[:300]} for c in calls])
    return entry


def judged_counts(task: dict) -> tuple[int, int]:
    """(judged, failed) for a QuickICP task: its reuse check + every bought round (latest loop snapshot)."""
    steps = get_steps(task["id"])
    judged = failed = 0
    reuse = [s for s in steps if s["step"] == "reuse" and s["status"] == "done"]
    if reuse:
        out = reuse[-1]["output"] or {}
        passed = out.get("passed")
        passed = passed if isinstance(passed, int) else len(passed or [])
        judged += out.get("existing") or 0
        failed += (out.get("existing") or 0) - passed
    snapshots = [s for s in steps if s["step"] == "state" and s["status"] == "done" and (s["output"] or {}).get("loop")]
    if snapshots:
        all_judged = snapshots[-1]["output"]["loop"].get("all_judged") or []
        judged += len(all_judged)
        failed += sum(1 for c in all_judged if (c.get("validation") or {}).get("status") != "pass")
    return judged, failed


def data_quality(tasks: list[dict]) -> dict:
    """Code: % landline, % without email, % failed the judge; flags at or above THRESHOLDS."""
    signals = {}
    for task in tasks:
        if task["status"] != "done" or task["agent"] != "QuickICP":
            continue
        judged, failed = judged_counts(task)
        if task["output_type"] == "accounts" and judged:
            signals["companies_failed_judge_pct"] = pct(failed, judged)
            signals["companies_judged"] = judged
        if task["output_type"] == "contacts":
            r = task["result"] or {}
            if judged:
                signals["people_failed_judge_pct"] = pct(failed, judged)
                signals["people_judged"] = judged
            phones = (r.get("mobile") or 0) + (r.get("landline") or 0) + (r.get("unknown_phone") or 0)
            if phones:
                signals["landline_pct"] = pct(r.get("landline") or 0, phones)
            if task["achieved_number"]:
                signals["without_email_pct"] = pct(task["achieved_number"] - (r.get("with_email") or 0),
                                                   task["achieved_number"])
    flags = [{"signal": name, "value_pct": signals[name], "threshold_pct": limit}
             for name, limit in THRESHOLDS.items() if signals.get(name) is not None and signals[name] >= limit]
    return {**signals, "thresholds_pct": THRESHOLDS, "flagged": flags}


def review_numbers(run: dict) -> dict:
    """Every number of the review (except objections, added after labelling). Code only."""
    tasks = get_tasks(run["id"])
    by_status = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    return {
        "run_key": run["run_key"], "test_mode": run["test_mode"],
        "tasks_by_status": by_status,
        "open_decisions": len(list_open_decisions(run["id"])),
        "stopped_tasks": [f"{t['task_id']}: {t['last_error']}" for t in tasks if t["status"] == "stopped"],
        "ready_next": [t["task_id"] for t in ready_tasks(run["id"])],
        "tool_tasks": [task_numbers(t) for t in tasks
                       if (t["agent"], t["output_type"]) in TOOL_TASKS and t["status"] == "done"],
        "data_quality": data_quality(tasks),
        "email_results": NOT_AVAILABLE,
    }


# ================= OBJECTIONS: LLM labels, code counts =================

def call_records(run: dict) -> list[dict]:
    """Every finished call of the run's done calls tasks: from the latest ledger poll (with transcripts)."""
    records = []
    for task in get_tasks(run["id"]):
        if task["output_type"] != "calls" or task["status"] != "done":
            continue
        polls = [s for s in get_steps(task["id"]) if s["step"] == "poll" and s["status"] == "done"]
        calls = list((polls[-1]["output"] or {}).get("calls", {}).values()) if polls else []
        if not calls:                                     # no poll rows: the outcomes in the result
            calls = [{"contact": {"name": o.get("name"), "company": o.get("company")}, "summary": o.get("summary"),
                      "outcome": o.get("outcome"), "transcript": []} for o in (task["result"] or {}).get("outcomes", [])]
        for call in calls:
            records.append({"task_id": task["task_id"], "name": (call.get("contact") or {}).get("name"),
                            "outcome": call.get("outcome"), "text": call_text(call)})
    return records


def call_text(call: dict) -> str:
    """Summary + transcript, the PROSPECT's lines first, cut to TRANSCRIPT_CHARS."""
    lines = call.get("transcript") or []
    prospect = [f"Prospect: {l.get('text') or l.get('message') or ''}" for l in lines if l.get("speaker") == "user"]
    caller = [f"Caller: {l.get('text') or l.get('message') or ''}" for l in lines if l.get("speaker") != "user"]
    text = "\n".join([f"Summary: {call.get('summary') or ''}", f"Outcome: {call.get('outcome')}"] + prospect + caller)
    return text[:TRANSCRIPT_CHARS]


def label_calls(records: list[dict], ask=ask_llm) -> tuple[dict, int]:
    """({counts, quotes}, LLM calls). The LLM labels each call; code counts; unknown labels -> 'other'."""
    labels, calls_used = {}, 0
    for start in range(0, len(records), LABEL_BATCH):
        batch = records[start:start + LABEL_BATCH]
        prompt = ("Label why each sales call ended the way it did. For EACH call pick exactly ONE label from: "
                  + ", ".join(OBJECTION_LABELS) + ". Give a short quote of the prospect's own words (max 15 words).\n\n"
                  + json.dumps([{"index": start + i, "call": r["text"]} for i, r in enumerate(batch)], ensure_ascii=False))
        answer = ask(CallLabels, prompt, "Call objections")
        calls_used += 1
        for item in answer.labels:
            labels[item.index] = item
    counts, quotes = {}, []
    for i, record in enumerate(records):
        item = labels.get(i)
        label = item.label if item and item.label in OBJECTION_LABELS else "other"
        counts[label] = counts.get(label, 0) + 1
        quotes.append({"task_id": record["task_id"], "name": record["name"], "label": label,
                       "quote": (item.quote if item else "")[:150]})
    return {"calls_labelled": len(records), "counts": counts, "quotes": quotes}, calls_used


# ================= TEXT (LLM) + CHECKS (code) =================

def normalise(number: str) -> str:
    """'35.0' -> '35', '3.50' -> '3.5'."""
    return number.rstrip("0").rstrip(".") if "." in number else number


def numbers_in(text: str) -> set[str]:
    return {normalise(n) for n in re.findall(r"\d+(?:\.\d+)?", text or "")}


def invented_numbers(text: ReviewText, allowed: set[str]) -> list[str]:
    """Numbers in the LLM's text that are not in the data it was given (0-3 always allowed)."""
    found = numbers_in(json.dumps(text.model_dump(), ensure_ascii=False))
    return sorted(found - allowed - FREE_NUMBERS, key=lambda n: (len(n), n))


# Claims of an EVENT the text may only make when RESULTS (code data, never the plan goals) show it:
# name -> (claim in the text, evidence in RESULTS). A negated sentence ('no demo was booked') is fine.
CLAIMS = {
    "demo or meeting booked": (
        re.compile(r"\b(demo|meeting|appointment)s?\b[^.;!?]{0,40}\b(scheduled|booked|confirmed|arranged)\b"
                   r"|\b(scheduled|booked|confirmed|arranged)\b[^.;!?]{0,40}\b(demo|meeting|appointment)s?\b",
                   re.IGNORECASE),
        re.compile(r"(demo|meeting|appointment)[ _-]?(scheduled|booked|confirmed|arranged)", re.IGNORECASE)),
    "deal or order won": (
        re.compile(r"\b(deal|order|contract|sale)s?\b[^.;!?]{0,30}\b(won|closed|signed)\b"
                   r"|\b(won|closed|signed)\b[^.;!?]{0,30}\b(deal|order|contract|sale)s?\b", re.IGNORECASE),
        re.compile(r"(deal|order|contract)[ _-]?(won|closed|signed)|closed[ _-]won", re.IGNORECASE)),
}
NEGATION = re.compile(r"\b(no|not|none|never|zero|without|yet to)\b|n't\b", re.IGNORECASE)


def text_sentences(text: ReviewText) -> list[str]:
    """Every sentence that states a fact: what_worked, whats_short and each action's 'why'.
    The action itself is a suggestion ('Get a demo booked with ...'), not a claim, so it is not checked."""
    parts = text.what_worked + text.whats_short + [a.why for a in text.next_actions]
    return [s.strip() for part in parts for s in re.split(r"(?<=[.;!?])\s+", part or "") if s.strip()]


def unsupported_claims(text: ReviewText, results: dict) -> list[str]:
    """
    Sentences claiming an event (demo/meeting booked, deal won) that RESULTS do not show, e.g.
    'demo scheduled' when the call outcome is only 'interested'. Negated sentences are allowed. Code decides.
    """
    evidence = json.dumps(results, ensure_ascii=False)
    found = []
    for sentence in text_sentences(text):
        for name, (claim, proof) in CLAIMS.items():
            if claim.search(sentence) and not NEGATION.search(sentence) and not proof.search(evidence):
                found.append(f"{name}: '{sentence}'")
    return found


def rule_key(rule: str) -> str:
    return re.sub(r"\s+", " ", (rule or "").strip().lower()).rstrip(".")


def unmatched_rules(text: ReviewText, plan_rules: list[str]) -> list[str]:
    """follows_rule values that are neither 'new' nor one of the plan's optimization rules."""
    known = {rule_key(r) for r in plan_rules}
    return [a.follows_rule for a in text.next_actions if rule_key(a.follows_rule) not in known | {"new"}]


def review_prompt(numbers: dict, plan: dict) -> str:
    """What the LLM sees: the numbers (code), the objections, the data quality, and the plan's goals."""
    goals = {k: plan.get(k) for k in ("month", "objectives", "kpis_to_track", "optimization_rules")}
    return (
        "You are the Master Agent of MOTM Revenue OS reviewing a running month plan for the MOTM team.\n"
        "Rules:\n"
        "- Use ONLY the numbers in RESULTS. Never invent, estimate or calculate new numbers.\n"
        + ("- TEST MODE IS ON: effective_target is a deliberate small test cap. NEVER suggest scaling volume "
           "towards plan_target_number and never call low volume a problem. Focus on QUALITY: data quality, "
           "who we reach, what they said (objections), the call script and targeting.\n" if numbers["test_mode"] else "")
        + "- Email opens and replies are not available yet; say so, never guess them.\n"
        "- data_quality.flagged lists signals at or above their threshold: suggest a concrete fix for them.\n"
        "- objections: counts of why calls ended, with the prospect's quotes: suggest script or targeting "
        "changes based on them.\n"
        f"- what_worked and whats_short: at most 3 short points each. next_actions: at most {MAX_ACTIONS}, concrete, "
        "each with: task_id (e.g. T7, or 'run'), kind (quality, script, targeting, data_quality, process, volume), "
        "and follows_rule = ONE rule from PLAN GOALS.optimization_rules copied EXACTLY, or 'new' if none fits.\n"
        "- Plain words for a busy reader.\n\n"
        "PLAN GOALS:\n" + json.dumps(goals, ensure_ascii=False)
        + "\n\nRESULTS (computed by code):\n" + json.dumps(numbers, ensure_ascii=False)
    )


def apply_code_rules(text: ReviewText, test_mode: bool) -> ReviewText:
    """TEST_MODE: drop 'volume' actions and say once that volume shortfalls are test caps. Max 3 actions."""
    if test_mode:
        text.next_actions = [a for a in text.next_actions if a.kind != "volume"]
        text.whats_short = [TEST_CAP_LINE] + [p for p in text.whats_short if p != TEST_CAP_LINE]
    text.next_actions = text.next_actions[:MAX_ACTIONS]
    return text


def write_review_text(numbers: dict, plan: dict, ask=ask_llm) -> tuple[ReviewText | None, str, int]:
    """(text or None, note, LLM calls used). One retry if the text invents numbers, claims or rules."""
    prompt = review_prompt(numbers, plan)
    allowed = numbers_in(prompt)
    plan_rules = plan.get("optimization_rules") or []
    feedback, invented, claims, unmatched = "", [], [], []
    for attempt in (1, 2):
        text = ask(ReviewText, prompt + feedback, "Run review")
        invented, unmatched = invented_numbers(text, allowed), unmatched_rules(text, plan_rules)
        claims = unsupported_claims(text, numbers)
        if not invented and not claims and not unmatched:
            return apply_code_rules(text, numbers["test_mode"]), "", attempt
        feedback = "\n\nYOUR PREVIOUS ANSWER HAD PROBLEMS:"
        if invented:
            feedback += f"\n- numbers that are not in RESULTS or PLAN GOALS: {invented}; use only numbers given."
        if claims:
            feedback += (f"\n- claims RESULTS do not show: {claims}; describe only what RESULTS say "
                         "(e.g. outcome 'interested' is not a scheduled demo).")
        if unmatched:
            feedback += f"\n- follows_rule values that are not plan rules: {unmatched}; copy one rule EXACTLY or use 'new'."
    if invented or claims:
        problems = ([f"invented numbers {invented}"] if invented else []) + ([f"claims not in the data {claims}"] if claims else [])
        return None, f"explanation rejected: {'; '.join(problems)} (after one retry); numbers-only review", 2
    for action in text.next_actions:
        if action.follows_rule in unmatched:
            action.follows_rule = "new (rule not matched)"
    return apply_code_rules(text, numbers["test_mode"]), f"follows_rule not matched for {unmatched}: marked 'new'", 2


# ================= SAVE =================

def open_review_actions(run: dict) -> list[dict]:
    return [d for d in list_open_decisions(run["id"]) if d["kind"] in REVIEW_ACTION_KINDS]


def save_review(run: dict, numbers: dict, text: ReviewText | None, note: str) -> dict:
    """Open the action decisions, record the review, add a 'review' entry to each reviewed task."""
    reviewed_at = now_iso()
    action_ids = []
    for kind, action in zip(REVIEW_ACTION_KINDS, text.next_actions if text else []):
        decision = open_decision(run["id"], kind, {
            "text": f"Suggested action ({action.task_id}, {action.kind}): {action.action} | why: {action.why} | "
                    f"follows plan rule: {action.follows_rule} | accept / reject, with a note. Nothing runs automatically.",
            "action": action.action, "why": action.why, "task_id": action.task_id, "kind": action.kind,
            "follows_rule": action.follows_rule, "reviewed_at": reviewed_at})
        action_ids.append(decision["id"])
    summary = (f"review of {len(numbers['tool_tasks'])} done tool task(s); {len(action_ids)} action(s) suggested"
               + (f"; {note}" if note else ""))
    review = record_decision(run["id"], "run_review", "saved", "brain", summary, question={
        "numbers": numbers, "explanation": text.model_dump() if text else None, "note": note,
        "action_decision_ids": action_ids, "reviewed_at": reviewed_at})
    by_id = {entry["task_id"]: entry for entry in numbers["tool_tasks"]}
    for task in get_tasks(run["id"]):
        if task["task_id"] in by_id:
            update_task(task["id"], result={**(task["result"] or {}), "review": {
                "review_decision_id": review["id"], "reviewed_at": reviewed_at, "numbers": by_id[task["task_id"]]}})
    return {"review": review, "action_ids": action_ids}


def review_run(run: dict, ask=ask_llm) -> dict:
    """The whole review (the caller holds the run lock). Returns numbers, text, note, saved ids, LLM calls."""
    still_open = open_review_actions(run)
    if still_open:
        raise ToolError("STOPPED: answer the open review actions first: "
                        + ", ".join(f"#{d['id']} ({d['kind']})" for d in still_open)
                        + ". python orchestrator.py decisions")
    numbers = review_numbers(run)
    if not numbers["tool_tasks"]:
        raise ToolError(f"STOPPED: run {run['run_key']} has no done tool task to review yet.")
    records = call_records(run)
    numbers["objections"], label_calls_used = label_calls(records, ask) if records else ({"calls_labelled": 0,
                                                                                         "counts": {}, "quotes": []}, 0)
    text, note, review_calls = write_review_text(numbers, run["plan"] or {}, ask)
    saved = save_review(run, numbers, text, note)
    return {"numbers": numbers, "text": text, "note": note, "llm_calls": label_calls_used + review_calls, **saved}


def print_review(result: dict) -> None:
    """A short table of the numbers, data quality, objections, the explanation and the actions."""
    numbers = result["numbers"]
    print(f"\nREVIEW {numbers['run_key']}" + (" (TEST MODE: effective targets are test caps)" if numbers["test_mode"] else ""))
    print(f"  Tasks: {numbers['tasks_by_status']} | open decisions: {numbers['open_decisions']} | "
          f"ready next: {', '.join(numbers['ready_next']) or 'none'}")
    print(f"\n  {'task':<5} {'output':<9} {'achieved':>8} {'target':>7} {'%':>5} {'plan':>6} {'% plan':>7}  details")
    for t in numbers["tool_tasks"]:
        extra = {k: v for k, v in t.items() if k in ("reused", "bought", "with_email", "with_phone", "mobile",
                                                     "landline", "recipients", "campaign_status", "outcome_counts",
                                                     "skipped_landlines", "rejected_by_quickhowl",
                                                     "rejected_do_not_call")}
        print(f"  {t['task_id']:<5} {t['output_type']:<9} {str(t['achieved']):>8} {str(t['effective_target']):>7} "
              f"{str(t['pct_of_effective_target']):>5} {str(t['plan_target_number']):>6} {str(t['pct_of_plan_target']):>7}  {extra}")
        if t["output_type"] == "emails":
            print(f"        email opens / replies: {NOT_AVAILABLE}")
    dq = numbers["data_quality"]
    print("\n  Data quality: " + ", ".join(f"{k} {dq[k]}%" for k in THRESHOLDS if dq.get(k) is not None))
    for flag in dq["flagged"]:
        print(f"    FLAGGED: {flag['signal']} {flag['value_pct']}% (threshold {flag['threshold_pct']}%)")
    objections = numbers.get("objections") or {}
    if objections.get("calls_labelled"):
        print(f"  Call objections ({objections['calls_labelled']} call(s)): {objections['counts']}")
        for q in objections["quotes"][:5]:
            print(f"    {q['label']}: \"{q['quote']}\" ({q['name']})")
    text = result["text"]
    if text:
        print("\n  What worked:")
        for point in text.what_worked:
            print(f"    - {point}")
        print("  What is short:")
        for point in text.whats_short:
            print(f"    - {point}")
    if result["note"]:
        print(f"\n  NOTE: {result['note']}")
    if result["action_ids"]:
        print("\n  Suggested actions (nothing runs automatically):")
        for decision_id, action in zip(result["action_ids"], text.next_actions):
            print(f"    #{decision_id} [{action.task_id}, {action.kind}] {action.action}")
            print(f"        why: {action.why}")
            print(f"        follows plan rule: {action.follows_rule}")
            print(f'        answer: python orchestrator.py answer {decision_id} <accept | reject> "note"')
    print(f"\n  Saved: run_review decision #{result['review']['id']} | LLM calls: {result['llm_calls']}")
