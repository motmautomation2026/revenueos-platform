"""
lead_qual/selftest.py  -  python -m lead_qual selftest. A fake LLM and a temp folder: no network, no LLM, nothing
written outside the temp folder.
"""

import contextlib
import io
import json
import os
import tempfile

from lead_qual import check, criteria, extract, regions, store

DOCUMENT = ("Anvil Steel. We manufacture TMT bars in steel grade 1 to 10 for builders. We deliver across North India "
            "(Punjab, Haryana, Delhi, Uttar Pradesh). We serve construction and infrastructure companies. We do not "
            "serve the defence industry. We take orders from 5 to 500 tonnes.")
PAN_INDIA = DOCUMENT.replace("We deliver across North India (Punjab, Haryana, Delhi, Uttar Pradesh)", "We deliver pan-India")


def found_criteria(delivery_quote="We deliver across North India", served=("north",), not_served=()):
    """What the fake LLM answers for the document (+ one product the document never mentions)."""
    C = criteria
    return C.Criteria(
        products=[C.Product(name="TMT bars", quotes=["We manufacture TMT bars"],
                            specs=[C.Spec(name="steel grade", min=1, max=10, quotes=["steel grade 1 to 10"])]),
                  C.Product(name="Steel pipes", quotes=["We also make steel pipes"], specs=[])],
        delivery=C.Delivery(served=list(served), not_served=list(not_served), quotes=[delivery_quote]),
        industries=C.Industries(allowed=["construction", "infrastructure"], excluded=["defence"],      # 2 pieces
                                quotes=["We serve construction and infrastructure companies",
                                        "We do not serve the defence industry"]),
        order_size=C.OrderSize(text="from 5 to 500 tonnes", quotes=["orders from 5 to 500 tonnes"]),
        weights=[C.Weight(criterion="product", weight=10, hard=True), C.Weight(criterion="spec steel grade", weight=8, hard=True),
                 C.Weight(criterion="delivery", weight=9, hard=True), C.Weight(criterion="industry", weight=7, hard=True),
                 C.Weight(criterion="order size", weight=5)],
        questions_for_cam=["Do you deliver to Rajasthan?"], lead_questions=["Which state is the site in?"])


# The real anvixa-ai-product case (2026-10-16): these 4 lines of data/intake_document.txt, verbatim, and what the LLM
# found for the 3 items that the old quote check dropped (their 'quotes' and 'as_found', verbatim).
REAL_DOCUMENT = "\n".join([
    "- Average order value: ₹30 lakh to ₹2 crore per project/deal (source: checklist:Q-34)",
    "- Q-12 [Ideal customers] Which of the following industries are your top priority for new business? (Select all "
    "that apply) -> Manufacturing, Supply Chain, Logistics, E-commerce, Healthcare, Energy, Industrial operations, "
    "Enterprise operations",
    "- Q-13 [Ideal customers] Are there any industries or segments you want MOTM to avoid or deprioritise? -> Small and "
    "medium-scale companies.",
    "- Q-14 [Ideal customers] What is the minimum and maximum company size (by employee count or turnover) you want to "
    "target? -> Large/billion-dollar companies with a good IT budget and more than 200 SAP users. Typical project "
    "value: ₹30 lakh to ₹2 crore."])
REAL_FOUND = {
    "industries": {"allowed": ["Manufacturing", "Supply Chain", "Logistics", "E-commerce", "Healthcare", "Energy",
                               "Industrial operations", "Enterprise operations"],
                   "excluded": ["Small and medium-scale companies"],
                   "quotes": ["- Manufacturing, Supply Chain, Logistics, E-commerce, Healthcare, Energy, Industrial "
                              "operations, Enterprise operations", "- Small and medium-scale companies."]},
    "company_size": {"min_employees": None, "max_employees": None, "excluded": ["small and medium-scale companies"],
                     "quotes": ["- Large/billion-dollar companies with a good IT budget and more than 200 SAP users.",
                                "- Small and medium-scale companies."]},
    "order_size": {"text": "₹30 lakh to ₹2 crore",
                   "quotes": ["- Typical project value: ₹30 lakh to ₹2 crore.",
                              "- Average order value: ₹30 lakh to ₹2 crore per project/deal"]},
}


def cam_answers_questions(key):
    """What the CAM does before approving: answer the open questions in the criteria, then remove them."""
    data = store.read(store.criteria_path(key), "test")
    data["questions_for_cam"] = []
    store.write(store.criteria_path(key), data)


# What the fake LLM reads from each lead's text (only what it is asked for is used; quotes are checked in code).
LEAD_FACTS = {
    "grade 11": dict(product="TMT bars", product_quote="TMT bars", specs=[("steel grade", "11", "grade 11")],
                     state="Punjab", location_quote="Ludhiana, Punjab", industry="construction",
                     industry_quote="builder", quantity=100, quantity_unit="tonnes", quantity_quote="100 tonnes"),
    "Chennai": dict(product="TMT bars", product_quote="TMT bars", specs=[("steel grade", "8", "grade 8")],
                    state="Tamil Nadu", city="Chennai", location_quote="Chennai, Tamil Nadu", industry="construction",
                    industry_quote="builder", quantity=50, quantity_unit="tonnes", quantity_quote="50 tonnes"),
    "good": dict(product="TMT bars", product_quote="TMT bars", specs=[("steel grade", "8", "grade 8")],
                 state="Punjab", location_quote="Amritsar, Punjab", industry="construction", industry_quote="builder",
                 quantity=100, quantity_unit="tonnes", quantity_quote="100 tonnes"),
    "defence": dict(product="TMT bars", product_quote="TMT bars", specs=[("steel grade", "8", "grade 8")],
                    state="Haryana", location_quote="Panipat, Haryana", industry="defence",
                    industry_quote="defence contractor", quantity=40, quantity_unit="tonnes", quantity_quote="40 tonnes"),
    "no grade": dict(product="TMT bars", product_quote="TMT bars", specs=[], state="Delhi", location_quote="Delhi",
                     industry="construction", industry_quote="builder", quantity=60, quantity_unit="tonnes",
                     quantity_quote="60 tonnes"),
    "invented": dict(product="TMT bars", product_quote="TMT bars", specs=[("steel grade", "8", "grade 8")],
                     state="Punjab", location_quote="somewhere in Punjab", industry="construction",
                     industry_quote="builder", quantity=70, quantity_unit="tonnes", quantity_quote="70 tonnes"),
}
LEADS = [
    {"lead_id": "L1", "source": "web", "text": "builder in Ludhiana, Punjab needs 100 tonnes TMT bars grade 11"},
    {"lead_id": "L2", "source": "web", "text": "builder in Chennai, Tamil Nadu: 50 tonnes TMT bars grade 8"},
    {"lead_id": "L3", "source": "web", "text": "good lead: builder from Amritsar, Punjab, 100 tonnes TMT bars grade 8"},
    {"lead_id": "L4", "source": "expo", "text": "defence contractor in Panipat, Haryana, 40 tonnes TMT bars grade 8"},
    {"lead_id": "L5", "source": "web", "text": "no grade given: builder in Delhi, 60 tonnes TMT bars"},
    {"lead_id": "L6", "source": "web", "text": "invented location: builder, 70 tonnes TMT bars grade 8",
     "company": "Given Co"},
    {"lead_id": "L7", "source": "crm", "text": "good: builder, TMT bars grade 8, 100 tonnes, Amritsar, Punjab",
     "state": "Haryana", "industry": "infrastructure"},
]


def fake_batch(calls):
    def ask_batch(schema, prompts, name):
        calls.append(len(prompts))
        answers = []
        for prompt in prompts:
            text = prompt.split("LEAD:\n", 1)[1]
            key = next((k for k in LEAD_FACTS if k in text), None)
            facts = dict(LEAD_FACTS[key]) if key else {}                  # a lead it knows nothing about
            facts["specs"] = [extract.SpecValue(name=n, value=v, quote=q) for n, v, q in facts.get("specs", [])]
            answers.append(schema(**facts))
        return answers
    return ask_batch


def selftest() -> bool:
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    def stops(function) -> str:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                function()
        except store.Stop as error:
            return str(error)
        return ""

    from intake_adapter import rupees_per_unit  # noqa: F401  (its load_dotenv runs NOW, not after PLAN_APPROVER is cleared)
    saved_cwd, saved_env = os.getcwd(), {k: os.environ.get(k) for k in ("CUSTOMER_KEY", "PLAN_APPROVER")}
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.chdir(folder)
            os.environ["CUSTOMER_KEY"] = "selftest-leadqual"
            os.environ.pop("PLAN_APPROVER", None)
            os.makedirs("data")
            open(store.DOCUMENT, "w", encoding="utf-8").write(DOCUMENT)
            key = store.customer()

            data = criteria.build(key, ask=lambda schema, prompt, name: found_criteria(), understanding="")
            saved = store.read(store.criteria_path(key), "test")
            expect("criteria: saved as a DRAFT in data/maps/selftest_leadqual_lead_criteria.json; every kept item has a "
                   "word-for-word quote; 'Steel pipes' (quote not in the document) is DROPPED with a question",
                   saved["status"] == "draft" and [p["name"] for p in saved["criteria"]["products"]] == ["TMT bars"]
                   and [d["item"] for d in data["dropped"] if "quotes" in d] == ["product 'Steel pipes'"]
                   and any("Steel pipes" in q for q in data["questions_for_cam"])
                   and "Do you deliver to Rajasthan?" in data["questions_for_cam"]
                   and data["lead_questions"] == ["Which state is the site in?"])
            expect("approve is BLOCKED while questions_for_cam are open; lead_questions never block",
                   "open question for the CAM" in stops(lambda: criteria.approve(key, ask=lambda _: "yes"))
                   and "Which state is the site in?" not in stops(lambda: criteria.approve(key, ask=lambda _: "yes")))
            cam_answers_questions(key)

            expect("check before approval -> refused ('not approved')", "not approved" in stops(lambda: criteria.approved(key)))
            answers = iter(["no"])
            expect("approve: anything but 'yes' -> nothing changes",
                   "not approved" in stops(lambda: criteria.approve(key, ask=lambda _: next(answers)))
                   and store.read(store.criteria_path(key), "t")["status"] == "draft")
            edited = store.read(store.criteria_path(key), "t")
            edited["criteria"]["products"].append({"name": "Wire rods", "quote": "", "source": "document", "specs": []})
            store.write(store.criteria_path(key), edited)
            expect("approve: an added item with no document quote and not marked source 'cam' -> refused",
                   "Wire rods" in stops(lambda: criteria.approve(key, ask=lambda _: "yes")))
            edited["criteria"]["products"][-1]["source"] = "cam"
            store.write(store.criteria_path(key), edited)
            answers = iter(["yes", "Asha Rao"])
            with contextlib.redirect_stdout(io.StringIO()):
                approved = criteria.approve(key, ask=lambda _: next(answers))
            expect("approve: 'yes' + name -> approved, approved_by, sha256; the CAM-added item (source 'cam') is kept",
                   approved["status"] == "approved" and approved["approval"]["approved_by"] == "Asha Rao"
                   and approved["approval"]["sha256"] == store.fingerprint(approved["criteria"])
                   and criteria.approved(key)["criteria"]["products"][-1]["name"] == "Wire rods")

            calls = []
            result = check.check(LEADS, key, criteria.approved(key), fake_batch(calls))
            rows = {r["lead_id"]: r for r in result["leads"]}
            reason = lambda lead, name: next(r for r in rows[lead]["results"] if r["criterion"] == name)
            expect("steel grade 11 against 1..10 -> spec fails -> not_verified",
                   reason("L1", "spec steel grade")["result"] == "fail" and rows["L1"]["verdict"] == "not_verified")
            expect("delivery to Tamil Nadu for a North-only supplier -> fails -> not_verified",
                   reason("L2", "delivery")["result"] == "fail" and "south" in reason("L2", "delivery")["reason"]
                   and rows["L2"]["verdict"] == "not_verified")
            expect("everything in the criteria -> verified", rows["L3"]["verdict"] == "verified")
            expect("excluded industry (defence) -> fails -> not_verified",
                   reason("L4", "industry")["result"] == "fail" and rows["L4"]["verdict"] == "not_verified")
            expect("missing steel grade -> unknown -> needs_human, with the question",
                   rows["L5"]["verdict"] == "needs_human" and rows["L5"]["questions"] == ["What steel grade does this lead need?"])
            expect("an LLM value whose quote is not in the lead's text is thrown away (location -> unknown, needs_human)",
                   reason("L6", "delivery")["result"] == "unknown" and rows["L6"]["verdict"] == "needs_human")
            expect("given fields WIN over the LLM: L7 gives state Haryana (text says Punjab) and industry infrastructure",
                   rows["L7"]["facts"]["state"] == {"value": "Haryana", "from": "given"}
                   and rows["L7"]["facts"]["industry"]["value"] == "infrastructure" and rows["L7"]["verdict"] == "verified")
            expect("ONE batched LLM call per lead that misses something (7 leads -> 7 prompts in one batch)",
                   calls == [7] and result["llm_calls"] == 7 and os.path.exists(store.VERDICTS))

            no_specs = {**criteria.approved(key)["criteria"],
                        "products": [{"name": "TMT bars", "quote": "We manufacture TMT bars", "source": "document", "specs": []}]}
            complete = {"lead_id": "L9", "text": "x", "product": "TMT bars", "state": "Punjab", "industry": "construction",
                        "quantity": "200 tonnes"}
            calls = []
            facts, made = extract.extract([complete], no_specs, fake_batch(calls))
            expect("a lead that gives every needed field -> 0 LLM calls; '200 tonnes' read by code",
                   made == 0 and calls == [] and facts[0]["quantity"] == {"value": 200.0, "unit": "tonnes", "from": "given"}
                   and check.verdict(check.judge(facts[0], no_specs), no_specs["weights"]) == "verified")
            kg = check.judge({**facts[0], "quantity": {"value": 200, "unit": "kg"}}, no_specs)
            expect("a different unit (200 kg vs tonnes) -> unknown, never a guess",
                   next(r for r in kg if r["criterion"] == "order size")["result"] == "unknown")

            first = json.load(open(store.VERDICTS, encoding="utf-8"))
            again = check.check(LEADS[:2], key, criteria.approved(key), fake_batch([]))
            archived = again["archived_previous"]
            expect("a second check MOVES the previous verdicts to data/archive/<time>-leads/ (never overwritten)",
                   archived and os.path.dirname(archived).startswith(os.path.join("data", "archive"))
                   and os.path.dirname(archived).endswith("-leads")
                   and json.load(open(archived, encoding="utf-8")) == first
                   and len(json.load(open(store.VERDICTS, encoding="utf-8"))["leads"]) == 2)

            row = check.answer("L2", "verified", "We can ship to Chennai by rail this month", by="Asha Rao")
            again_answer = stops(lambda: check.answer("L2", "not_verified", "changed my mind"))
            expect("answer: a not_verified lead -> human_verdict, note, answered_by, answered_at saved; a second answer "
                   "-> refused ('already answered')",
                   row["human_verdict"] == "verified" and row["answered_by"] == "Asha Rao" and row["answered_at"]
                   and json.load(open(store.VERDICTS, encoding="utf-8"))["leads"][1]["human_note"].startswith("We can ship")
                   and "already answered" in again_answer)
            store.write(store.VERDICTS, result | {"leads": result["leads"]})
            expect("answer: no note, an unknown lead, a verified lead, or a word other than verified / not_verified -> "
                   "refused",
                   "note is required" in stops(lambda: check.answer("L5", "verified", "  "))
                   and "no lead" in stops(lambda: check.answer("L99", "verified", "x"))
                   and "verified by the checks" in stops(lambda: check.answer("L3", "not_verified", "x"))
                   and "answer verified or not_verified" in stops(lambda: check.answer("L5", "maybe", "x")))

            changed = store.read(store.criteria_path(key), "t")
            changed["criteria"]["order_size"]["max"] = 5000
            store.write(store.criteria_path(key), changed)
            expect("criteria edited after approval -> check refused ('changed after they were approved')",
                   "changed after they were approved" in stops(lambda: criteria.approved(key)))

            open(store.DOCUMENT, "w", encoding="utf-8").write(PAN_INDIA)
            criteria.build(key, ask=lambda s, p, n: found_criteria("We deliver pan-India", ("pan-India",)), understanding="")
            cam_answers_questions(key)
            answers = iter(["yes", "Asha Rao"])
            with contextlib.redirect_stdout(io.StringIO()):
                criteria.approve(key, ask=lambda _: next(answers))
            pan = check.check(LEADS[1:2], key, criteria.approved(key), fake_batch([]))["leads"][0]
            expect("the same Tamil Nadu lead PASSES delivery when the document says pan-India",
                   next(r for r in pan["results"] if r["criterion"] == "delivery")["result"] == "pass"
                   and pan["verdict"] == "verified")
            # ---- fixes 1-7: dropped items keep their quotes, 1-3 pieces, ₹ / Rs / INR / '�', order size by code,
            #      company size, prompt rules ----
            first_build = criteria.keep_quoted(found_criteria(), DOCUMENT)
            pipes = next(d for d in first_build[1] if d["item"] == "product 'Steel pipes'" and "quotes" in d)
            piece = next(d for d in first_build[1] if d["item"] == "product 'Steel pipes'" and "piece" in d)
            expect("a dropped item keeps the LLM's quotes, what it found and the reason (so a person can fix it)",
                   pipes["quotes"] == ["We also make steel pipes"] and pipes["as_found"]["name"] == "Steel pipes"
                   and pipes["reason"] == "no piece is word for word in the document"
                   and piece["reason"] == "not word for word in the document: 'We also make steel pipes'")
            two = criteria.Industries(excluded=["defence"], quotes=["We serve construction", "We do not serve the defence industry"])
            half = criteria.Industries(excluded=["defence"], quotes=["We serve construction", "We never sell to the army"])
            expect("1-3 pieces: two exact pieces from different sentences -> kept; one piece not in the document -> "
                   "dropped; 4 pieces -> dropped",
                   first_build[0]["industries"]["quotes"] == found_criteria().industries.quotes
                   and criteria.quote_problem(two.quotes, DOCUMENT) is None
                   and "not word for word" in criteria.quote_problem(half.quotes, DOCUMENT)
                   and "more than 3" in criteria.quote_problem(["We serve construction"] * 4, DOCUMENT))
            expect("quotes: ₹, Rs, Rs., INR and the broken '�' count as the same mark; nothing else changes "
                   "('workers' keeps its 'rs')",
                   criteria.quote_problem(["Rs 30 lakh to Rs. 2 crore"], "AOV ₹30 lakh to ₹2 crore") is None
                   and criteria.quote_problem(["INR 30 lakh per deal"], "AOV �30 lakh per deal") is None
                   and criteria.quote_problem(["₹31 lakh per deal"], "AOV ₹30 lakh per deal") is not None
                   and criteria.squash("Workers") == "workers")
            ranges = [criteria.amount_range(t) for t in ("₹30 lakh to ₹2 crore", "30 to 200 lakh ₹", "5 to 500 tonnes",
                                                          "minimum ₹10 lakh", "up to 50 tonnes", "about fifty")]
            expect("order size by CODE: '₹30 lakh to ₹2 crore' and '30 to 200 lakh ₹' -> 3000000-20000000 INR; "
                   "'5 to 500 tonnes'; 'minimum ₹10 lakh'; 'up to 50 tonnes'; words -> None (a question)",
                   ranges == [{"min": 3e6, "max": 2e7, "unit": "INR"}, {"min": 3e6, "max": 2e7, "unit": "INR"},
                              {"min": 5, "max": 500, "unit": "tonnes"}, {"min": 1e6, "max": None, "unit": "INR"},
                              {"min": None, "max": 50, "unit": "tonnes"}, None])
            money = {"order_size": {"min": 3e6, "max": 2e7, "unit": "INR"}}
            amount = lambda value, unit: check.judge({"quantity": {"value": value, "unit": unit}}, money)[0]["result"]
            given = extract.given_facts({"quantity": "₹50 lakh"})["quantity"]
            expect("an INR order size: the lead's lakh / crore / ₹ amount is converted by code (50 lakh pass, 10 lakh "
                   "fail, 5 crore fail, 'Rs' pass); no unit -> unknown; a given '₹50 lakh' is read as 50 lakh",
                   [amount(50, "lakh ₹"), amount(10, "lakh"), amount(5, "crore"), amount(4000000, "Rs"), amount(50, "")]
                   == ["pass", "fail", "fail", "pass", "unknown"]
                   and given == {"value": 50.0, "unit": "lakh", "from": "given"}
                   and check.judge({"quantity": given}, money)[0]["result"] == "pass")
            low_reason = check.judge({"quantity": {"value": 10, "unit": "lakh"}}, money)[0]["reason"]
            expect("amounts print readably, never 1e+06: 'order size ₹10 lakh (1,000,000 INR) is outside ₹30 lakh "
                   "(3,000,000 INR) .. ₹2 crore (20,000,000 INR)'; 1.5 crore, ₹5,000 and 500 tonnes too",
                   low_reason == "order size ₹10 lakh (1,000,000 INR) is outside ₹30 lakh (3,000,000 INR) .. "
                                 "₹2 crore (20,000,000 INR)"
                   and "e+" not in low_reason
                   and check.readable(15_000_000, "INR") == "₹1.5 crore (15,000,000 INR)"
                   and check.readable(5000, "INR") == "₹5,000 (5,000 INR)"
                   and check.readable(500, "tonnes") == "500 tonnes")
            sized = {"company_size": {"excluded": ["small and medium"], "min_employees": None, "max_employees": None}}
            ranged = {"company_size": {"excluded": [], "min_employees": 200, "max_employees": None}}
            size = lambda facts, rule: check.judge(facts, rule)[0]["result"]
            expect("company size (separate from industry): 'SME' vs excluded 'small and medium' -> fail; 'large "
                   "enterprise' -> pass; 150 employees vs min 200 -> fail, 1200 -> pass; nothing known -> unknown; a "
                   "given 'employees' field is used",
                   size({"company_size": {"value": "SME"}}, sized) == "fail"
                   and size({"company_size": {"value": "large enterprise"}}, sized) == "pass"
                   and size({"employees": {"value": 150}}, ranged) == "fail"
                   and size({"employees": extract.given_facts({"employees": "1,200"})["employees"]}, ranged) == "pass"
                   and size({}, sized) == "unknown"
                   and "company_size" in extract.missing({"text": "x"}, sized)
                   and "company_size" not in extract.missing({"text": "x", "employees": "300"}, sized))
            expect("the criteria prompt: only HARD restrictions (priorities are not), specs a LEAD can state (not "
                   "product features), company size separate, order size copied as text, questions on lead fit only",
                   all(words in criteria.PROMPT for words in (
                       "Only HARD restrictions", "priority regions, segments or tiers are NOT restrictions",
                       "things a LEAD can state", "never features of the customer's own product",
                       "keep it separate from industries", "copy the text exactly", "ONLY about whether a lead fits")))

            # ---- products are picked like industries; an optional not_offered list; a delivery country ----
            menu = {"products": [{"name": "AI-powered Browser Agent", "specs": []}],
                    "not_offered": {"names": ["Hardware"]}}
            product = lambda value: check.judge({"product": {"value": value}} if value else {}, menu)[0]["result"]
            expect("product: one of the names (or a given name that contains it) -> pass; 'other: ...' -> unknown "
                   "(needs_human, never fail); no product -> unknown; only a 'not_offered' product -> fail",
                   [product("AI-powered Browser Agent"), product("Browser Agent"), product("other: CRM software"),
                    product(None), product("other: Hardware"), product("Hardware")]
                   == ["pass", "pass", "unknown", "unknown", "fail", "fail"]
                   and check.judge({"product": {"value": "other: CRM software"}}, menu)[0]["result"] == "unknown")
            asked = extract.prompt_for({"text": "x"}, menu, ["product"])
            expect("the product question lists the criteria's names and offers 'other: <what it is>'",
                   "AI-powered Browser Agent" in asked and "Hardware" in asked and "'other: <what it is>'" in asked)
            india = {"delivery": {"served": ["India"], "not_served": []}}
            place = lambda facts: check.judge(facts, india)[0]["result"]
            expect("delivery: a country outside India (Dubai, UAE) -> fail; India + Maharashtra -> pass; India and no "
                   "state -> unknown; the LLM is asked for the state unless the lead gives a foreign country",
                   place({"country": {"value": "UAE"}, "city": {"value": "Dubai"}}) == "fail"
                   and place({"country": {"value": "India"}, "state": {"value": "Maharashtra"}}) == "pass"
                   and place({"country": {"value": "India"}}) == "unknown"
                   and "state" in extract.missing({"text": "x", "country": "India"}, india)
                   and "state" not in extract.missing({"text": "x", "country": "UAE"}, india))
            kept3, dropped3, _ = criteria.keep_quoted(criteria.Criteria(not_offered=criteria.NotOffered(
                names=["pipes", "wire rods"], quotes=["We do not make pipes"])), "Anvil Steel. We do not make pipes.")
            expect("not_offered follows the quote rules: a name in the quoted piece is kept, one that is not is dropped",
                   kept3["not_offered"]["names"] == ["pipes"]
                   and ("not_offered", "wire rods") in {(d["item"], d.get("value")) for d in dropped3})

            # ---- scoring: weights, score %, thresholds (config), hard criteria ----
            from lead_qual import config

            def scored(*rows):
                """rows: (result, weight, hard) -> decide() on made-up criteria c0, c1, ..."""
                results = [{"criterion": f"c{i}", "result": r, "reason": ""} for i, (r, _, _) in enumerate(rows)]
                return check.decide(results, {f"c{i}": {"weight": w, "hard": h} for i, (_, w, h) in enumerate(rows)})
            at = {"80": scored(("pass", 80, False), ("fail", 20, False)),
                  "79.9": scored(("pass", 799, False), ("fail", 201, False)),
                  "60": scored(("pass", 60, False), ("fail", 40, False)),
                  "59.9": scored(("pass", 599, False), ("fail", 401, False)),
                  "95 hard": scored(("pass", 95, False), ("fail", 5, True))}
            expect("boundaries: 80% -> verified, 79.9% -> needs_human, 60% -> needs_human, 59.9% -> not_verified, a hard "
                   "fail at 95% -> not_verified",
                   [(k, v["verdict"]) for k, v in at.items()] == [("80", "verified"), ("79.9", "needs_human"),
                                                                ("60", "needs_human"), ("59.9", "not_verified"),
                                                                ("95 hard", "not_verified")]
                   and at["95 hard"]["score"] == 95 and at["95 hard"]["reason"].startswith("hard criterion failed: c1"))
            capped = scored(("pass", 90, False), ("unknown", 10, True))
            expect("a HARD criterion unknown caps the lead at needs_human (95% here, never verified); the same unknown "
                   "when not hard -> verified",
                   capped["score"] == 95 and capped["verdict"] == "needs_human"
                   and "hard criterion is unknown: c1" in capped["reason"]
                   and scored(("pass", 90, False), ("unknown", 10, False))["verdict"] == "verified")
            half = scored(("pass", 50, False), ("unknown", 50, False))
            expect("unknown counts HALF its weight (50 pass + 50 unknown -> 75%); nothing to score -> needs_human; a "
                   "79.95% score shows as '79.9%' (rounded down) and is needs_human",
                   half["score"] == 75 and half["verdict"] == "needs_human"
                   and check.decide([], {})["verdict"] == "needs_human" and check.shown(79.95) == "79.9%"
                   and scored(("pass", 7995, False), ("fail", 2005, False))["verdict"] == "needs_human")
            saved_at = config.VERIFIED_AT
            try:
                config.VERIFIED_AT = 90.0
                stricter = scored(("pass", 85, False), ("fail", 15, False))["verdict"]
            finally:
                config.VERIFIED_AT = saved_at
            expect("the thresholds come from lead_qual/config.py (VERIFIED_AT 90 -> an 85% lead needs a person)",
                   stricter == "needs_human" and (config.VERIFIED_AT, config.NEEDS_HUMAN_AT) == (80.0, 60.0))

            no_weights = criteria.Criteria(
                products=[criteria.Product(name="TMT bars", quotes=["We manufacture TMT bars"])],
                order_size=criteria.OrderSize(text="from 5 to 500 tonnes", quotes=["orders from 5 to 500 tonnes"]),
                weights=[criteria.Weight(criterion="product", weight=3, hard=True),
                         criteria.Weight(criterion="colour", weight=2)])
            built, built_dropped, built_questions = criteria.keep_quoted(no_weights, DOCUMENT)
            expect("weights from the LLM are kept; a criterion it did not weigh gets weight 1, not hard (source 'code') "
                   "+ a question for the CAM; a weight for a criterion that does not exist is dropped",
                   built["weights"]["product"] == {"weight": 3.0, "hard": True, "source": "llm", "reason": ""}
                   and built["weights"]["order size"]["source"] == "code" and built["weights"]["order size"]["weight"] == 1
                   and any(q.startswith("Confirm the weight of 'order size'") for q in built_questions)
                   and ("weights", "colour") in {(d["item"], d.get("value")) for d in built_dropped})
            bad = {"products": [{"name": "x", "specs": []}], "order_size": {"text": "5"},
                   "weights": {"product": {"weight": 0, "hard": True}, "order size": {"weight": "abc", "hard": "yes"}}}
            expect("weights that cannot be used are refused at approval: 0, a word, 'hard' not true/false, a criterion "
                   "with no weight",
                   len(criteria.weight_problems(bad)) == 3
                   and criteria.weight_problems({**bad, "weights": {"product": {"weight": 2, "hard": False}}})
                   == ["no weight for 'order size'"])
            old = store.read(store.criteria_path(key), "t")
            old["criteria"].pop("weights")
            old["approval"]["sha256"] = store.fingerprint(old["criteria"])         # approved BEFORE scoring existed
            store.write(store.criteria_path(key), old)
            old_message = stops(lambda: criteria.approved(key))
            current = store.read(store.criteria_path(key), "t")
            criteria.build(key, ask=lambda s, p, n: found_criteria("We deliver pan-India", ("pan-India",)), understanding="")
            cam_answers_questions(key)
            answers = iter(["yes", "Asha Rao"])
            with contextlib.redirect_stdout(io.StringIO()):
                criteria.approve(key, ask=lambda _: next(answers))
            reweighed = store.read(store.criteria_path(key), "t")
            original_weight = reweighed["criteria"]["weights"]["order size"]["weight"]
            reweighed["criteria"]["weights"]["order size"]["weight"] = 9
            store.write(store.criteria_path(key), reweighed)
            expect("criteria approved before scoring (no weights) -> 'approve again' message; a weight changed after "
                   "approval -> refused (weights are part of the sha256)",
                   "approved before scoring and have no weights" in old_message and "weights" not in current["criteria"]
                   and "changed after they were approved" in stops(lambda: criteria.approved(key)))
            reweighed["criteria"]["weights"]["order size"]["weight"] = original_weight
            store.write(store.criteria_path(key), reweighed)
            scored_row = check.check(LEADS[:1], key, criteria.approved(key), fake_batch([]))["leads"][0]
            expect("each lead in the verdicts file has its score, earned / total, the shown % and the reason",
                   scored_row["verdict"] == "not_verified" and scored_row["score_shown"].endswith("%")
                   and scored_row["total"] == 39
                   and scored_row["verdict_reason"].startswith("hard criterion failed: spec steel grade"))

            # ---- the lead inbox: calls from the ledger, manual / tele-calling, de-dup, check-inbox, answer ----
            from types import SimpleNamespace
            from lead_qual import inbox

            def call(call_id, outcome, name, company, phone, said):
                return {"call_id": call_id, "outcome": outcome, "status": "completed", "ended_at": "2026-10-20T10:00:00Z",
                        "summary": said, "contact": {"name": name, "company": company, "phone": phone},
                        "transcript": [{"speaker": "agent", "text": "Hello, calling about TMT bars."},
                                       {"speaker": "user", "text": said}]}
            live_calls = [call("c1", "interested", "Ravi Kumar", "Anvil Builders", "+91 98765 43210", LEADS[2]["text"]),
                          call("c2", "callback_requested", "Meena Rao", "Coastal Homes", "+91 98111 22233", LEADS[1]["text"]),
                          call("c3", "not_interested", "Ajay Shah", "Shah Infra", "+91 98222 33344", "not now"),
                          call("c4", "pending", "Neha Jain", "Jain Estates", "+91 98333 44455", "")]
            test_calls = [call("t1", "interested", "Ravi Kumar", "Anvil Builders", "+91 90000 00001", LEADS[2]["text"]),
                          call("t2", "interested", "Kiran Patel", "Patel Steel Works", "+91 90000 00001", LEADS[0]["text"])]
            polls = {11: [{"step": "poll", "status": "done", "output": {"calls": {"c1": live_calls[0]}}},
                          {"step": "poll", "status": "done", "output": json.dumps({"calls": {c["call_id"]: c for c in live_calls}})},
                          {"step": "poll", "status": "failed", "output": None}],
                     12: [{"step": "poll", "status": "done", "output": {"calls": {c["call_id"]: c for c in test_calls}}}]}
            reads = SimpleNamespace(
                get_runs=lambda k: [{"id": 1, "run_key": f"{k}-2026-10", "test_mode": False},
                                    {"id": 2, "run_key": f"{k}-2026-11", "test_mode": True}],
                get_tasks=lambda run_id: [{"id": 10 + run_id, "task_id": "T7", "agent": "Quickhowl", "output_type": "calls"},
                                          {"id": 30, "task_id": "T2", "agent": "CAM", "output_type": "coordination"}],
                get_steps=lambda task_row_id: polls.get(task_row_id, []))
            first = inbox.import_calls(key, "2026-10", "T7", reads)
            leads_now = store.read_inbox()["leads"]
            ravi = leads_now[0]
            expect("import-calls reads the LATEST done 'poll' row of the ledger (no tool call): only interested / "
                   "callback_requested become leads; text = summary + transcript; phone kept in a live run",
                   first == {"added": 2, "already": 0, "duplicate": 0, "skipped (other outcome)": 2}
                   and [(l["lead_id"], l["source"], l["source_ref"]) for l in leads_now] == [("LQ-0001", "call", "c1"),
                                                                                           ("LQ-0002", "call", "c2")]
                   and ravi["text"].startswith("Call summary: good lead") and "\nlead: good lead" in ravi["text"]
                   and ravi["phone"] == "+91 98765 43210" and ravi["test_mode"] is False)
            expect("importing the same calls again changes nothing (same source_ref = no-op)",
                   inbox.import_calls(key, "2026-10", "T7", reads)["already"] == 2 and len(store.read_inbox()["leads"]) == 2)
            in_test = inbox.import_calls(key, "2026-11", "T7", reads)
            kiran = store.read_inbox()["leads"][-1]
            expect("a TEST-MODE run: the phone (ours) is left out and the lead is marked test_mode; de-dup falls back to "
                   "company + name (Ravi again -> a duplicate of LQ-0001, not a new lead)",
                   in_test["added"] == 1 and in_test["duplicate"] == 1 and kiran["test_mode"] is True and kiran["phone"] is None
                   and store.read_inbox()["leads"][0]["duplicates"][0]["source_ref"] == "t1")
            expect("import-calls refuses a task that is not Quickhowl calls, and a month with no run",
                   "not a Quickhowl calls task" in stops(lambda: inbox.import_calls(key, "2026-10", "T2", reads))
                   and "no run" in stops(lambda: inbox.import_calls(key, "2027-01", "T7", reads)))
            by_phone = inbox.manual("telecalling", {"name": "R. Kumar", "company": "Other name", "phone": "098765-43210",
                                                    "text": "called back, still keen"})
            new = inbox.manual("telecalling", {"name": "Sunil Verma", "company": "Verma Defence Works", "email": "sv@verma.test",
                                               "text": LEADS[3]["text"]})
            again = inbox.manual("telecalling", {"name": "Sunil Verma", "company": "Verma Defence Works",
                                                 "email": "sv@verma.test", "text": LEADS[3]["text"]})
            by_email = inbox.manual("manual", {"name": "S Verma", "email": "SV@verma.test", "text": "emailed us too"})
            expect("add: a phone written differently -> duplicate by phone; a new person -> added; the same add twice -> "
                   "no-op; the same email in other case -> duplicate by email",
                   (by_phone[0], by_phone[1]["lead_id"], by_phone[2]) == ("duplicate", "LQ-0001", "phone")
                   and (new[0], new[1]["lead_id"]) == ("added", "LQ-0004") and again[0] == "already"
                   and (by_email[0], by_email[2]) == ("duplicate", "email"))
            expect("add refuses: no text; no phone, email or name + company; a source other than telecalling / manual",
                   "needs its text" in stops(lambda: inbox.manual("manual", {"name": "A", "company": "B", "text": " "}))
                   and "phone, an email, or a name and a company" in stops(lambda: inbox.manual("manual", {"name": "A", "text": "x"}))
                   and "telecalling or manual" in stops(lambda: inbox.manual("call", {"name": "A", "company": "B", "text": "x"})))
            calls_made = []
            checked = inbox.check_inbox(key, criteria.approved(key), fake_batch(calls_made))
            leads_now = {l["lead_id"]: l for l in store.read_inbox()["leads"]}
            expect("check-inbox checks every unchecked lead once (same rules: verdict + score), saved with the lead",
                   [l["lead_id"] for l in checked] == ["LQ-0001", "LQ-0002", "LQ-0003", "LQ-0004"] and calls_made == [4]
                   and leads_now["LQ-0001"]["check"]["verdict"] == "verified"
                   and leads_now["LQ-0003"]["check"]["verdict"] == "not_verified"
                   and leads_now["LQ-0003"]["check"]["score_shown"].endswith("%")
                   and leads_now["LQ-0004"]["check"]["verdict_reason"].startswith("hard criterion failed: industry"))
            later = inbox.manual("telecalling", {"name": "Ravi K", "company": "x", "phone": "9876543210", "text": "one more call"})
            expect("check-inbox never re-checks: a second run checks nothing; a later duplicate does not trigger a check",
                   inbox.check_inbox(key, criteria.approved(key), fake_batch(calls_made)) == [] and calls_made == [4]
                   and later[0] == "duplicate"
                   and store.read_inbox()["leads"][0]["check"]["checked_at"] == leads_now["LQ-0001"]["check"]["checked_at"])
            row = check.answer("LQ-0003", "verified", "Patel Steel can take grade 8 instead", by="Asha Rao")
            saved_check = next(l for l in store.read_inbox()["leads"] if l["lead_id"] == "LQ-0003")["check"]
            pending = inbox.manual("manual", {"name": "Zara Khan", "company": "Khan Metals", "text": "new enquiry"})
            expect("answer works on an inbox lead (saved in its check, once); an unchecked inbox lead -> 'check-inbox first'",
                   row["human_verdict"] == "verified" and saved_check["answered_by"] == "Asha Rao"
                   and "already answered" in stops(lambda: check.answer("LQ-0003", "not_verified", "x"))
                   and "not checked yet" in stops(lambda: check.answer(pending[1]["lead_id"], "verified", "x")))
            approved_before = criteria.approved(key)["approval"]["sha256"]
            first_checks = {l["lead_id"]: l["check"] for l in store.read_inbox()["leads"] if l.get("check")}
            changed = store.read(store.criteria_path(key), "t")
            changed["criteria"]["weights"]["order size"]["weight"] = 6.0      # the CAM changes the criteria ...
            store.write(store.criteria_path(key), changed)
            answers = iter(["yes", "Asha Rao"])
            with contextlib.redirect_stdout(io.StringIO()):
                criteria.approve(key, ask=lambda _: next(answers))           # ... and approves them again
            plain_run = inbox.check_inbox(key, criteria.approved(key), fake_batch([]))
            redone = inbox.check_inbox(key, criteria.approved(key), fake_batch([]), recheck=True)
            after = {l["lead_id"]: l for l in store.read_inbox()["leads"]}
            expect("check-inbox --recheck: re-checks leads checked against OTHER criteria (sha256) that no person "
                   "answered; the old check is kept in previous_checks; an answered lead is never re-checked; without "
                   "--recheck only new leads are checked; a second --recheck finds nothing",
                   [l["lead_id"] for l in plain_run] == ["LQ-0005"]
                   and [l["lead_id"] for l in redone] == ["LQ-0001", "LQ-0002", "LQ-0004"]
                   and after["LQ-0001"]["previous_checks"] == [first_checks["LQ-0001"]]
                   and after["LQ-0001"]["check"]["criteria_sha256"] != approved_before
                   and "previous_checks" not in after["LQ-0003"] and after["LQ-0003"]["check"] == saved_check
                   and inbox.check_inbox(key, criteria.approved(key), fake_batch([]), recheck=True) == [])
            from lead_qual.cli import inbox_summary
            expect("check-inbox summary: '1 new lead(s) checked.' / '3 lead(s) re-checked.' / '1 new, 3 re-checked.' / "
                   "nothing -> 'No new leads: every inbox lead is checked.'",
                   inbox_summary(plain_run) == "1 new lead(s) checked." and inbox_summary(redone) == "3 lead(s) re-checked."
                   and inbox_summary(plain_run + redone) == "1 new, 3 re-checked."
                   and inbox_summary([]) == "No new leads: every inbox lead is checked.")
            before = store.read_inbox()
            real_dump = store.json.dump

            def half_written(data, file, **options):
                file.write('{"leads": [')
                raise OSError("disk full (simulated)")
            store.json.dump = half_written
            try:
                store.save_inbox({"leads": []})
            except OSError:
                pass
            finally:
                store.json.dump = real_dump
            expect("a write that breaks half way never damages the inbox (temp file, then rename)",
                   store.read_inbox() == before)

            # ---- a low score with nothing failed; product aliases ----
            expect("a score under 60 is not_verified ONLY if something failed: only unknowns (50%) -> needs_human; an "
                   "unknown and a fail (25%) -> not_verified",
                   scored(("unknown", 50, False), ("unknown", 50, False))["verdict"] == "needs_human"
                   and "nothing failed" in scored(("unknown", 50, False), ("unknown", 50, False))["reason"]
                   and scored(("unknown", 50, False), ("fail", 50, False))["verdict"] == "not_verified")
            aliased = {"products": [{"name": "AI-powered Browser Agent", "aliases": ["browser automation", "RPA"], "specs": []}]}
            pick = lambda value: check.judge({"product": {"value": value}}, aliased)[0]["result"]
            expect("product aliases: an alias -> pass (also as 'other: <exactly an alias>'); anything else stays unknown; "
                   "the product question shows the aliases but asks for the name; aliases are in the sha256",
                   [pick("browser automation"), pick("other: RPA"), pick("other: CRM software"), pick("AI-powered Browser Agent")]
                   == ["pass", "pass", "unknown", "pass"]
                   and "AI-powered Browser Agent (leads may also say: browser automation, RPA)"
                   in extract.prompt_for({"text": "x"}, aliased, ["product"])
                   and store.fingerprint(aliased) != store.fingerprint({"products": [{**aliased["products"][0], "aliases": []}]}))

            # ---- the 3 items dropped in the real anvixa-ai-product build (their quotes and as_found, verbatim) ----
            real = criteria.Criteria(industries=criteria.Industries(**REAL_FOUND["industries"]),
                                     company_size=criteria.CompanySize(**REAL_FOUND["company_size"]),
                                     order_size=criteria.OrderSize(**REAL_FOUND["order_size"]))
            kept, real_dropped, real_questions = criteria.keep_quoted(real, REAL_DOCUMENT)
            expect("real file: with list markers ('- ') and the end '.' removed, all 5 pieces match the document; "
                   "industries, company size and order size are now KEPT (nothing dropped as a whole)",
                   kept["industries"] and kept["company_size"] and kept["order_size"]
                   and not [d for d in real_dropped if "quotes" in d or "piece" in d]
                   and not [q for q in real_questions if not q.startswith("Confirm the weight of")])
            expect("real file: 'Small and medium-scale companies' (an excluded 'industry') is a company size -> moved to "
                   "company_size by code (once); the 8 industry names stay as industries; order size 3000000-20000000 INR",
                   kept["industries"]["excluded"] == [] and len(kept["industries"]["allowed"]) == 8
                   and kept["company_size"]["excluded"] == ["small and medium-scale companies"]
                   and any(d.get("reason", "").startswith("a company size, not an industry") for d in real_dropped)
                   and (kept["order_size"]["min"], kept["order_size"]["max"], kept["order_size"]["unit"]) == (3e6, 2e7, "INR"))
            expect("list markers and end punctuation: '- x.', '-> x;', '• x', '1. x', 'Q-12 [..] Which? -> x' -> 'x'; "
                   "'1.5 tonnes' is a number, not a marker; 'Enterprise operations' is not a size, 'SME' is",
                   [criteria.clean_piece(p) for p in ("- Logistics.", "-> Logistics;", "• Logistics", "1. Logistics",
                                                       "Q-12 [Ideal customers] Which? -> Logistics", "1.5 tonnes")]
                   == ["Logistics"] * 5 + ["1.5 tonnes"]
                   and not criteria.looks_like_size("Enterprise operations") and criteria.looks_like_size("SME"))
            mixed = criteria.Criteria(
                industries=criteria.Industries(allowed=REAL_FOUND["industries"]["allowed"] + ["Retail"], excluded=[],
                                               quotes=[REAL_FOUND["industries"]["quotes"][0], "- We never sell to retail."]),
                order_size=criteria.OrderSize(text="₹50 lakh to ₹5 crore", quotes=REAL_FOUND["order_size"]["quotes"]))
            kept2, dropped2, questions2 = criteria.keep_quoted(mixed, REAL_DOCUMENT)
            expect("per piece and per value: the one wrong piece is dropped, the item KEPT with its good piece; 'Retail' "
                   "(in no matched piece) dropped alone; an order text that is in no piece -> order size dropped + a "
                   "question for the CAM",
                   kept2["industries"]["quotes"] == [REAL_FOUND["industries"]["quotes"][0]]
                   and "Retail" not in kept2["industries"]["allowed"] and len(kept2["industries"]["allowed"]) == 8
                   and {(d["item"], d.get("piece") or d.get("value")) for d in dropped2}
                   == {("industries", "- We never sell to retail."), ("industries", "Retail"),
                       ("order_size", "₹50 lakh to ₹5 crore")}
                   and kept2["order_size"] is None and any("order_size" in q for q in questions2))

            expect("regions: 'not served' wins over pan-India; Delhi NCR is Delhi (north); an unknown place is not a state",
                   not regions.place_matches("Tamil Nadu", ["north"]) and regions.place_matches("Tamil Nadu", ["pan-India"])
                   and check.judge({"state": {"value": "Kerala"}}, {"delivery": {"served": ["pan-India"],
                                                                                "not_served": ["south"]}})[0]["result"] == "fail"
                   and regions.region_of("Delhi NCR") == "north" and not regions.is_state("Atlantis"))
        finally:
            os.chdir(saved_cwd)
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    ok = all(checks)
    print(f"LEAD QUAL SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok
