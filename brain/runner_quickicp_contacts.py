"""
runner_quickicp_contacts.py
===========================

Tool runner for QuickICP 'contacts' tasks (T4: "250-350 contacts mapped and validated"). Called by
orchestrator.py 'run'. Wraps people_loop.py (same rounds, same judge) and the email / phone finders,
with the brain's rules (runner_kit.py).

FLOW (each step recorded in brain_tool_calls)
----
    1. companies  FREE  the company ids T3 recorded for THIS run (its 'company_ids' output), read from
                        Supabase for THIS customer only, best industry-map score first
    2. titles     FREE  job titles to search (LLM, once)
    3. reuse      FREE  this customer's people already saved IN those companies, judged with the
                        current rules (validator.py). Done once, then reused.
    4. shortfall  = target - reused people who pass. 0 -> NO people search
    5. people     PAID  people search for the shortfall: round 0 + up to 2 extra rounds
                        (people_loop: more companies, then more people per company).
                        One row PER BATCH of 20 companies: <key>:people:r<round>:b<batch>:a<attempt>
    6. goal       still short -> goal_check (continue | more | stop); after 2 'more', or when
                        nothing is left to try -> goal_check_final (continue | stop)
    7. emails     PAID  email finder for PASSED people with NO email (list fixed in 'email_plan')
    8. phones     PAID  phone finder for PASSED people with NO phone (list fixed in 'phone_plan')
                        TEST_MODE: at most 6 people each (quickicp.TEST_MAX_EMAIL_PEOPLE)
    9. save       FREE  supabase_store.save_people(customer): new people inserted, EMPTY email/phone of
                        saved people filled (never overwritten); person ids + phone types recorded

CRASH SAFETY
------------
    a paid batch left 'running'          -> 'unknown' + paid_call_unknown decision (rerun | skip | stop);
                                            never repeated automatically
    a round redone after a crash         -> its FINISHED batches are replayed from their saved replies,
                                            never bought again (runner_kit.paid_batch)
    skip on a people batch               -> that batch counts as empty
    skip on an email / phone batch       -> those people simply stay without email / phone
    free steps (titles, reuse, plans, judge, save) -> repeated or reused, never a human question

TARGET (code, runner_kit.target_for)
------
    real: target_number (e.g. 350) | TEST_MODE: min(target_number, people_loop.TEST_PEOPLE_TARGET = 10)
    TEST_TARGET_OVERRIDE in .env (test mode only) wins.

OUTPUT (brain_tool_calls 'save' + the task result)
------
    company_ids, people_ids (owned_rows_output: customer + run_key), achieved, target, reused, bought,
    with_email, with_phone, mobile, landline, unknown_phone, phone_types {person_id: mobile|landline|unknown}

PLAN DATA
---------
    brain_customer_runs.understanding (runner_kit.run_understanding) + THIS task's work order.
    Never data/understanding.json or data/work_orders.json: those belong to the latest plan.

DEBUGGING
---------
    "STOPPED: ... needs the company ids of a done QuickICP accounts task"  -> run T3 first
    "STOPPED: none of T3's companies belong to customer ..."               -> check CUSTOMER_KEY
    Every batch: Supabase brain_tool_calls where idempotency_key like '<run_key>:T4:%'
"""

import math
import os

from people_loop import (MAX_EXTRA_ROUNDS, MAX_USER_RETRIES, TEST_PEOPLE_TARGET, LoopState, PeopleLoopTools,
                         companies_needed, count_passed, round_summary, run_extra_rounds, run_round,
                         something_left_to_try, starting_per_company)
from quickicp import (DOMAIN_BATCH_SIZE, PEOPLE_BATCH_SIZE, TEST_MAX_EMAIL_PEOPLE, TEST_MODE, ToolError,
                      clean_domain, save_json)
from runner_kit import RunnerContext, RunnerResult, target_for
from state_store import finish_step, get_tasks, mark_unknown, owned_rows_output
from supabase_store import phone_type

PAID_STEPS = ("people", "emails", "phones")
GOAL_KINDS = ("goal_check", "goal_check_final")


def linkedin_key(url: str | None) -> str:
    """'https://www.linkedin.com/company/Cipla/' -> 'linkedin.com/company/cipla' (same as the judge)."""
    from validator import linkedin_key as key
    return key(url)


def sendable(person: dict) -> dict:
    """A person as the finders expect it: our own fields ('_id', 'validation') removed."""
    return {k: v for k, v in person.items() if not k.startswith("_") and k != "validation"}


def enrich_cap(count: int) -> int:
    """TEST_MODE: at most TEST_MAX_EMAIL_PEOPLE people per finder."""
    return min(count, TEST_MAX_EMAIL_PEOPLE) if TEST_MODE else count


# ================= REAL TOOLS =================

class RealContactsTools(PeopleLoopTools):
    """The real calls: people search, people judge, email / phone finders, Supabase reads and save."""

    def __init__(self, run_key: str, task_id: str, understanding: dict, work_order: dict):
        """understanding: the run's (runner_kit.run_understanding); work_order: THIS task's. Never data/ files."""
        self.understanding, self.work_order = understanding, work_order
        folder = os.path.join("data", "runs", run_key)
        self.raw_file = os.path.join(folder, f"{task_id}_people_raw.json")
        self.log_file = os.path.join(folder, f"{task_id}_people_loop_log.json")
        self.company_keys: set[str] = set()
        self._industry_map = None

    def companies(self, customer: str, ids: list[int]) -> list[dict]:
        """Company rows with these ids that belong to THIS customer."""
        from supabase_store import COMPANY_TABLE, db
        return (db.table(COMPANY_TABLE).select("id, company_name, website, linkedin, industry")
                  .eq("customer", customer).in_("id", ids).execute().data)

    def company_score(self, company: dict) -> int:
        """The company's industry-map score (company override first). A lookup, no LLM."""
        from validator import entry_score, industry_key, load_map
        if self._industry_map is None:
            self._industry_map = load_map("industry")
        override = self._industry_map.get("company_overrides", {}).get(clean_domain(company.get("website")))
        entry = override or self._industry_map["items"].get(industry_key(company.get("industry")))
        return (entry_score(entry) if entry else None) or 0

    def job_titles(self, work_order: dict) -> list[str]:
        """Job titles to search (LLM)."""
        from quickicp import make_job_titles
        return make_job_titles(work_order)

    def existing_people(self, customer: str, companies: list[dict]) -> list[dict]:
        """This customer's people saved in these companies, in QuickICP field names (+ '_id')."""
        from supabase_store import PEOPLE_TABLE, db
        by_id = {c["id"]: c for c in companies}
        rows = (db.table(PEOPLE_TABLE).select("id, full_name, user_social, job_position, country, location, "
                                              "industry, company_id, email, phone_number")
                  .eq("customer", customer).in_("company_id", list(by_id)).execute().data)
        people = []
        for r in rows:
            c = by_id[r["company_id"]]
            people.append({"_id": r["id"], "FULL NAME": r["full_name"], "USER SOCIAL": r["user_social"],
                           "JOB POSITION": r["job_position"], "COUNTRY": r["country"], "LOCATION": r["location"],
                           "INDUSTRY": r["industry"], "COMPANY NAME": c["company_name"],
                           "COMPANY URL": c["website"], "COMPANY SOCIAL LINK": c["linkedin"],
                           "email": r["email"], "phone": r["phone_number"]})
        return people

    def judge(self, people: list[dict]) -> list[dict]:
        """People judge, with THIS run's T3 companies as the allowed companies and the run's context."""
        from validator import judge_people, people_context
        return judge_people(people, self.company_keys, context=people_context(self.understanding, self.work_order))

    def enrich(self, kind: str, people: list[dict]) -> list[dict]:
        """PAID email or phone finder for one batch."""
        from quickicp import EMAIL_WEBHOOK, PHONE_WEBHOOK, check_settings, enrich_people
        if kind == "emails":
            check_settings("QUICKICP_EMAIL_WEBHOOK_URL")
            return enrich_people("Email finder", EMAIL_WEBHOOK, people)
        check_settings("QUICKICP_PHONE_WEBHOOK_URL")
        return enrich_people("Phone finder", PHONE_WEBHOOK, people)

    def save_raw(self, people: list[dict]) -> None:
        save_json(self.raw_file, people)

    def save_log(self, log: list[dict]) -> None:
        save_json(self.log_file, log)

    def save_people(self, people: list[dict], customer: str) -> dict[str, int]:
        """Save for this customer (fill empty fields only); returns {LinkedIn URL: person row id}."""
        from supabase_store import company_lookups, save_people, saved_people
        save_people([sendable(p) for p in people], company_lookups(customer), customer)
        saved = saved_people(customer)
        return {p["USER SOCIAL"]: saved[p["USER SOCIAL"]]["id"] for p in people if p.get("USER SOCIAL") in saved}


# ================= FAKE TOOLS (selftest only: no network, no LLM) =================

class FakeContactsTools(PeopleLoopTools):
    """
    Canned companies and people for the orchestrator selftest. Counts every call. Can crash on purpose.
        companies     rows: id, customer, website, linkedin, company_name, industry
        scores        {company id: score}
        existing      people 'already in Supabase' (QuickICP fields + '_id', '_company_id')
        good_rate     every n-th found person passes (1 = all, 0 = none)
        crash_search  crash INSIDE people-search call n; crash_enrich = ('emails' | 'phones', call n)
        repeat_social the first person of the first search gets this (already known) LinkedIn URL
    """

    def __init__(self, companies, scores=None, existing=None, good_rate=1, crash_search=None,
                 crash_enrich=None, on_search=None, repeat_social=None):
        self.company_rows, self.scores = companies, scores or {}
        self.existing = existing or []
        self.good_rate, self.crash_search, self.crash_enrich = good_rate, crash_search, crash_enrich
        self.on_search, self.repeat_social = on_search, repeat_social
        self.calls = {"search": 0, "titles": 0, "emails": 0, "phones": 0, "save_people": 0}
        self.sent = {"emails": [], "phones": []}
        self.payloads = []
        self.made = 0
        self.company_keys: set[str] = set()

    @staticmethod
    def person(name: str, domain: str, good: bool, email=None, phone=None, _id=None, company_id=None) -> dict:
        p = {"FULL NAME": name, "USER SOCIAL": f"https://linkedin.com/in/{name}",
             "JOB POSITION": "Head Procurement" if good else "Sales Executive",
             "COMPANY URL": domain, "COMPANY NAME": domain, "email": email, "phone": phone}
        if _id is not None:
            p["_id"], p["_company_id"] = _id, company_id
        return p

    def companies(self, customer, ids):
        return [dict(c) for c in self.company_rows if c["customer"] == customer and c["id"] in ids]

    def company_score(self, company):
        return self.scores.get(company["id"], 0)

    def job_titles(self, work_order):
        self.calls["titles"] += 1
        return ["head procurement"]

    def existing_people(self, customer, companies):
        ids = {c["id"] for c in companies}
        return [dict(p) for p in self.existing if p.get("_company_id") in ids]

    def search_batch(self, payload, round_number, batch_number):
        from runner_quickicp_accounts import SimulatedCrash
        self.calls["search"] += 1
        self.payloads.append(payload.model_dump())
        if self.on_search:
            self.on_search()
        if self.crash_search == self.calls["search"]:
            raise SimulatedCrash("crash during the paid people search")
        found = []
        for domain in payload.domains:
            for _ in range(payload.count_per_company):
                self.made += 1
                good = bool(self.good_rate) and self.made % self.good_rate == 0
                found.append(self.person(f"p{self.made}", domain, good))
        if self.repeat_social and self.calls["search"] == 1 and found:
            found[0]["USER SOCIAL"] = self.repeat_social
        return found

    def judge(self, people):
        return [{**p, "validation": {"status": "pass" if p["JOB POSITION"].startswith("Head")
                                     and clean_domain(p.get("COMPANY URL")) in self.company_keys else "fail",
                                     "score": 90, "buying_role": "decision_maker", "reason": "fake",
                                     "checked_by": "llm"}} for p in people]

    def enrich(self, kind, people):
        from runner_quickicp_accounts import SimulatedCrash
        self.calls[kind] += 1
        if self.crash_enrich == (kind, self.calls[kind]):
            raise SimulatedCrash(f"crash during the paid {kind} finder")
        self.sent[kind] += [p["USER SOCIAL"] for p in people]
        out = []
        for i, p in enumerate(people):
            if kind == "emails":
                out.append({**p, "email": f"{p['FULL NAME']}@mail.test"})
            else:
                out.append({**p, "phone": f"+91987654{i:04d}" if i % 2 == 0 else f"+91112614{i:04d}"})
        return out

    def save_raw(self, people):
        pass

    def save_log(self, log):
        pass

    def save_people(self, people, customer):
        self.calls["save_people"] += 1
        self.saved = [dict(p) for p in people]
        return {p["USER SOCIAL"]: 800000 + i for i, p in enumerate(people)}


# ================= LEDGER AROUND THE LOOP =================

class LedgeredPeopleTools(PeopleLoopTools):
    """What people_loop's rounds see: each batch a PAID ledger row (finished ones replayed), state snapshots."""

    def __init__(self, inner, ctx: RunnerContext, progress: dict):
        self.inner, self.ctx, self.progress = inner, ctx, progress

    def search_batch(self, payload, round_number, batch_number):
        return self.ctx.paid_batch(f"people:r{round_number}:b{batch_number}",
                                   lambda: self.inner.search_batch(payload, round_number, batch_number),
                                   {"payload": payload.model_dump()}, self.progress["skipped"])

    def judge(self, people):
        return self.inner.judge(people)

    def save_raw(self, people):
        self.inner.save_raw(people)

    def save_log(self, log):
        self.inner.save_log(log)

    def after_round(self, state):
        self.save_snapshot(state)

    def save_snapshot(self, state: LoopState | None) -> None:
        snapshot = {"loop": state.to_json() if state else None, **self.progress}
        self.ctx.free_call("state", lambda: snapshot, seq=self.ctx.next_seq("state"))


# ================= THE RUNNER =================

def accounts_company_ids(ctx: RunnerContext) -> list[int]:
    """The company ids recorded by the done QuickICP accounts task this task depends on (T3)."""
    for dep in get_tasks(ctx.run["id"]):
        if (dep["task_id"] in ctx.task["depends_on"] and dep["agent"] == "QuickICP"
                and dep["output_type"] == "accounts" and dep["status"] == "done"):
            ids = (dep["result"] or {}).get("company_ids") or []
            if ids:
                return ids
    raise ToolError(f"STOPPED: {ctx.task['task_id']} needs the company ids of a done QuickICP accounts task "
                    f"it depends on (e.g. T3). Run that task first.")


def company_list(tools, customer: str, ids: list[int]) -> dict:
    """FREE: T3's companies for THIS customer only, best score first."""
    rows = tools.companies(customer, ids)
    scored = [{"id": r["id"], "website": clean_domain(r.get("website")), "linkedin": r.get("linkedin"),
               "company_name": r.get("company_name"), "industry": r.get("industry"),
               "score": tools.company_score(r)} for r in rows]
    scored.sort(key=lambda c: (-c["score"], c["id"]))
    return {"companies": scored, "dropped_other_customer": len(set(ids)) - len(rows)}


def reuse_people(tools, customer: str, companies: list[dict]) -> dict:
    """FREE: people already saved for this customer in these companies, judged with the current rules."""
    existing = tools.existing_people(customer, companies)
    judged = tools.judge(existing) if existing else []
    return {"existing": len(existing), "passed": [p for p in judged if p["validation"]["status"] == "pass"],
            "seen": sorted({p["USER SOCIAL"] for p in existing if p.get("USER SOCIAL")})}


def latest_unconsumed(ctx: RunnerContext, kinds: tuple[str, ...], consumed: list[int]) -> dict | None:
    """The newest decision of these kinds for this task that this runner has not acted on yet."""
    found = [d for kind in kinds for d in ctx.decisions(kind) if d["id"] not in consumed]
    return max(found, key=lambda d: d["id"]) if found else None


def batch_base(ctx: RunnerContext, key: str) -> str:
    """'<run_key>:T4:people:r0:b2:a1' -> 'people:r0:b2'."""
    return key[len(f"{ctx.run['run_key']}:{ctx.task['task_id']}:"):].rsplit(":a", 1)[0]


def chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def run_quickicp_contacts(ctx: RunnerContext, tools) -> RunnerResult:
    """One call of the contacts runner. Returns done / waiting / stopped / failed (or dry)."""
    task, customer = ctx.task, ctx.run["customer"]
    target = target_for(task, TEST_PEOPLE_TARGET)

    # A paid batch left 'running' by a crash -> 'unknown' (never repeated automatically).
    for row in ctx.steps():
        if row["step"] in PAID_STEPS and row["status"] == "running" and not ctx.dry:
            mark_unknown(row["id"], f"crash during the paid {row['step']} call (no reply saved)")
    unknown = [r for r in ctx.steps() if r["step"] in PAID_STEPS and r["status"] in ("unknown", "running")]
    if ctx.dry and unknown:
        return RunnerResult("dry", note=f"{task['task_id']} has an unresolved paid call ({unknown[-1]['idempotency_key']}); "
                                        "answer its paid_call_unknown decision first.")

    # 1-3. companies, titles, reuse (FREE, done once, then reused)
    ids = accounts_company_ids(ctx)
    found = ctx.free_call("companies", lambda: company_list(tools, customer, ids), tool="supabase")
    companies = found["companies"]
    if not companies:
        raise ToolError(f"STOPPED: none of T3's {len(ids)} companies belong to customer '{customer}'.")
    tools.company_keys = ({c["website"] for c in companies if c["website"]}
                          | {linkedin_key(c["linkedin"]) for c in companies if c["linkedin"]})
    queue = list(dict.fromkeys(c["website"] for c in companies if c["website"]))
    titles = ctx.free_call("titles", lambda: tools.job_titles(task["work_order"]))
    reuse = ctx.free_call("reuse", lambda: reuse_people(tools, customer, companies), tool="supabase")
    shortfall = max(0, target - len(reuse["passed"]))

    if ctx.dry:
        per = starting_per_company(shortfall, len(queue)) if shortfall else 0
        n_companies = min(len(queue), companies_needed(shortfall, per)) if shortfall else 0
        missing_email = sum(1 for p in reuse["passed"] if not p.get("email"))
        missing_phone = sum(1 for p in reuse["passed"] if not p.get("phone"))
        emails, phones = enrich_cap(missing_email + shortfall), enrich_cap(missing_phone + shortfall)
        lines = [f"Target:                 {target} passed people",
                 f"Companies from T3:      {len(companies)} for this customer"
                 + (f" ({found['dropped_other_customer']} ids of another customer ignored)" if found["dropped_other_customer"] else ""),
                 f"Already in Supabase:    {reuse['existing']} people in those companies, {len(reuse['passed'])} pass the judge",
                 f"Shortfall:              {shortfall}",
                 (f"Would buy:              ~{n_companies * per} people ({n_companies} companies x {per} each, "
                  f"{math.ceil(n_companies / DOMAIN_BATCH_SIZE)} batch(es))") if shortfall
                 else "Would buy:              NO people (reuse covers the target)",
                 f"                        {emails} email lookups ({missing_email} reused people without email"
                 + (f" + up to {shortfall} new" if shortfall else "") + (", test cap 6" if TEST_MODE else "") + ")",
                 f"                        {phones} phone lookups ({missing_phone} reused people without phone"
                 + (f" + up to {shortfall} new" if shortfall else "") + (", test cap 6" if TEST_MODE else "") + ")"]
        return RunnerResult("dry", achieved=len(reuse["passed"]), target=target, output={
            "shortfall": shortfall, "would_people": n_companies * per, "companies_to_search": n_companies,
            "per_company": per, "would_emails": emails, "would_phones": phones, "lines": lines})

    # Progress and loop state from the latest snapshot
    snapshots = [row for row in ctx.steps("state") if row["status"] == "done"]
    last = snapshots[-1]["output"] if snapshots else {}
    progress = {"consumed": list(last.get("consumed", [])), "mores": last.get("mores", 0),
                "skipped": list(last.get("skipped", []))}
    state = LoopState.from_json(last["loop"]) if last.get("loop") else None
    ledgered = LedgeredPeopleTools(tools, ctx, progress)

    # An unknown paid call: a human decides (rerun / skip / stop)
    if unknown:
        row = unknown[-1]
        decision = latest_unconsumed(ctx, ("paid_call_unknown",), progress["consumed"])
        if decision is None or decision["status"] == "open":
            return RunnerResult.waiting("paid_call_unknown", {
                "text": f"{task['task_id']}: the paid {row['step']} call {row['idempotency_key']} was interrupted; "
                        "we cannot know if it ran (and was charged). rerun = call again, skip = treat that batch "
                        "as empty, stop = stop this task.", "call": row["idempotency_key"]})
        progress["consumed"].append(decision["id"])
        finish_step(row["id"], "failed",
                    error=f"outcome unknown; human chose '{decision['answer']}' (decision #{decision['id']})")
        if decision["answer"] == "skip":
            progress["skipped"].append(batch_base(ctx, row["idempotency_key"]))
        ledgered.save_snapshot(state)
        if decision["answer"] == "stop":
            return RunnerResult.stopped(f"stopped after an interrupted paid call (decision #{decision['id']}): "
                                        f"{decision['reason']}")

    # 5-6. people search for the shortfall only
    if shortfall > 0:
        if state is None:
            state = LoopState(target=shortfall, queue=queue, job_titles=titles,
                              per_company=starting_per_company(shortfall, len(queue)), seen=set(reuse["seen"]))
        if not state.log:
            # Round 0 (after a crash it is redone the same way; its finished batches are replayed, not bought).
            domains = state.queue[:companies_needed(shortfall, state.per_company)]
            state.used = list(domains)
            run_round(0, "initial", domains, state, ledgered)
        while True:
            rounds_allowed = MAX_EXTRA_ROUNDS * (1 + progress["mores"])
            if count_passed(state.all_judged) < shortfall and state.rounds < rounds_allowed:
                run_extra_rounds(state, rounds_allowed - state.rounds, ledgered)
                ledgered.save_snapshot(state)
            if count_passed(state.all_judged) >= shortfall:
                break
            decision = latest_unconsumed(ctx, GOAL_KINDS, progress["consumed"])
            if decision is None or decision["status"] == "open":
                achieved = len(reuse["passed"]) + count_passed(state.all_judged)
                final = progress["mores"] >= MAX_USER_RETRIES or not something_left_to_try(state)
                kind = "goal_check_final" if final else "goal_check"
                ledgered.save_snapshot(state)
                return RunnerResult.waiting(kind, {
                    "text": f"{task['task_id']} people: {achieved} of {target} (short by {target - achieved}) after "
                            f"{state.rounds + 1} round(s). continue = go on with {achieved}"
                            + (", more = 2 more rounds" if kind == "goal_check" else
                               " (nothing left to try)" if not something_left_to_try(state) else "")
                            + ", stop = stop this task.",
                    "achieved": achieved, "target": target, "details": round_summary(state)})
            progress["consumed"].append(decision["id"])
            ledgered.save_snapshot(state)
            if decision["answer"] == "more":
                progress["mores"] += 1
                continue
            if decision["answer"] == "stop":
                return RunnerResult.stopped(f"stopped at the goal check (decision #{decision['id']}): {decision['reason']}")
            break

    # Everyone who passed: reused first, then new (new ones are never repeats: 'seen' started with the reused)
    new_passed = [p for p in (state.all_judged if state else []) if p["validation"]["status"] == "pass"]
    people = {p["USER SOCIAL"]: dict(p) for p in reuse["passed"] + new_passed if p.get("USER SOCIAL")}

    # 7-8. enrich ONLY passed people missing an email / a phone (lists fixed once, batches replayable)
    for kind, field_name, plan_step in (("emails", "email", "email_plan"), ("phones", "phone", "phone_plan")):
        plan = ctx.free_call(plan_step, lambda f=field_name: sorted(s for s, p in people.items() if not p.get(f))[
                                                             :enrich_cap(len(people))])
        for number, chunk in enumerate(chunks(plan, PEOPLE_BATCH_SIZE), start=1):
            records = ctx.paid_batch(f"{kind}:b{number}",
                                     lambda c=chunk, k=kind: tools.enrich(k, [sendable(people[s]) for s in c]),
                                     {"people": chunk}, progress["skipped"])
            for record in records:
                social = record.get("USER SOCIAL")
                if social in people and record.get(field_name) and not people[social].get(field_name):
                    people[social][field_name] = record[field_name]

    # 9. save for this customer; record which rows belong to this run
    saved = ctx.free_call("save", lambda: {"ids": tools.save_people(list(people.values()), customer)}, tool="supabase")
    ids_by_social = saved["ids"]
    types = {str(ids_by_social[s]): phone_type(p.get("phone")) for s, p in people.items() if s in ids_by_social}
    achieved = len(people)
    output = owned_rows_output(
        ctx.run, company_ids=[c["id"] for c in companies], people_ids=list(ids_by_social.values()),
        achieved=achieved, target=target, reused=len(reuse["passed"]), bought=len(new_passed),
        shortfall=max(0, target - achieved), with_email=sum(1 for p in people.values() if p.get("email")),
        with_phone=sum(1 for p in people.values() if p.get("phone")),
        mobile=sum(1 for t in types.values() if t == "mobile"), landline=sum(1 for t in types.values() if t == "landline"),
        unknown_phone=sum(1 for t in types.values() if t == "unknown"), phone_types=types,
        people_searches=len(ctx.steps("people")))
    note = (f"{achieved} of {target} people: {len(reuse['passed'])} reused, {len(new_passed)} new; "
            f"{output['with_email']} with email, {output['mobile']} mobile, {output['landline']} landline"
            + (f"; short by {target - achieved} (continued by a human)" if achieved < target else ""))
    return RunnerResult.done(achieved, target, output, note)
