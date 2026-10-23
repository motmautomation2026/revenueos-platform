"""
lead_qual/cli.py  -  the commands. Run from the brain folder (it reads ./data):

    python -m lead_qual criteria                                   build draft criteria (1 LLM call)
    python -m lead_qual approve                                    a person approves them (typed yes + name)
    python -m lead_qual check <leads.json>                         verdict per lead (<= 1 LLM call per lead)
    python -m lead_qual answer <lead_id> verified|not_verified "note"   a person decides a lead, once
    python -m lead_qual import-calls --run <month> <task>          interested / callback leads from the ledger
    python -m lead_qual add --source telecalling|manual --name .. --company .. --phone .. --text "..."
                                                                   (also --email --state --city --country --industry
                                                                    --product --quantity --employees --company-size --ref)
    python -m lead_qual check-inbox [--recheck]                    check the inbox leads not checked yet (--recheck:
                                                                   also those checked against older criteria, if no
                                                                   person answered them)
    python -m lead_qual selftest                                   fake LLM, temp folder: no network, no LLM
'criteria' and 'check' only run in test mode (quickicp.TEST_MODE). Nothing is written to Supabase.
"""

import json
import sys

from lead_qual import store

USAGE = __doc__.split("\n\n", 1)[1].split("'criteria'")[0].rstrip()


def test_mode_only() -> None:
    from quickicp import TEST_MODE
    if not TEST_MODE:
        raise store.Stop("STOPPED: lead_qual runs in test mode only (TEST_MODE is off).")


def parse_options(words: list[str]) -> dict:
    """['--source', 'manual', '--company-size', 'SME'] -> {'source': 'manual', 'company_size': 'SME'}."""
    fields, i = {}, 0
    while i < len(words):
        if not words[i].startswith("--") or i + 1 >= len(words):
            raise store.Stop(f"STOPPED: expected '--name value' pairs, got '{words[i]}'.")
        fields[words[i][2:].replace("-", "_")] = words[i + 1]
        i += 2
    return fields


def inbox_summary(checked: list[dict]) -> str:
    """check-inbox's summary: new leads and re-checked ones (--recheck; a re-checked lead keeps previous_checks)."""
    redone = sum(1 for lead in checked if lead.get("previous_checks"))
    new = len(checked) - redone
    if not checked:
        return "No new leads: every inbox lead is checked."
    if not redone:
        return f"{new} new lead(s) checked."
    return f"{redone} lead(s) re-checked." if not new else f"{new} new, {redone} re-checked."


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    try:
        if command == "selftest" and len(args) == 1:
            from lead_qual.selftest import selftest
            sys.exit(0 if selftest() else 1)
        elif command == "criteria" and len(args) == 1:
            from lead_qual.criteria import build
            test_mode_only()
            data = build(store.customer())
            c = data["criteria"]
            print(f"Draft criteria saved: {store.criteria_path(data['customer'])}")
            print(f"  products {len(c['products'])}, specs {sum(len(p['specs']) for p in c['products'])}, delivery "
                  f"{'yes' if c['delivery'] else 'no'}, industries {'yes' if c['industries'] else 'no'}, order size "
                  f"{'yes' if c['order_size'] else 'no'}, company size {'yes' if c['company_size'] else 'no'}; "
                  f"dropped {len(data['dropped'])}; questions for the CAM {len(data['questions_for_cam'])} (block "
                  f"approval), questions for leads {len(data['lead_questions'])}")
            for item in data["dropped"]:
                print(f"  dropped: {item['item']} {item.get('value') or item.get('piece') or ''} ({item['reason']})")
            for question in data["questions_for_cam"]:
                print(f"  question for the CAM: {question}")
            print("Next: answer the CAM questions in the file (edited items: \"source\": \"cam\"), remove them from "
                  "questions_for_cam, then: python -m lead_qual approve")
        elif command == "approve" and len(args) == 1:
            from lead_qual.criteria import approve
            data = approve(store.customer())
            print(f"Approved by {data['approval']['approved_by']} (sha256 {data['approval']['sha256'][:12]}...).")
        elif command == "check" and len(args) == 2:
            from lead_qual.check import check
            from lead_qual.criteria import approved
            test_mode_only()
            key = store.customer()
            criteria = approved(key)
            leads = json.load(open(args[1], encoding="utf-8"))
            data = check(leads, key, criteria)
            counts = {v: sum(1 for r in data["leads"] if r["verdict"] == v) for v in ("verified", "not_verified", "needs_human")}
            print(f"{len(data['leads'])} lead(s): {counts['verified']} verified, {counts['not_verified']} not_verified, "
                  f"{counts['needs_human']} needs_human ({data['llm_calls']} LLM call(s)). Saved: {store.VERDICTS}"
                  + (f" (previous moved to {data['archived_previous']})" if data["archived_previous"] else ""))
            for row in data["leads"]:
                reasons = "; ".join(f"{r['criterion']}: {r['reason'] or r['result']}" for r in row["results"]
                                    if r["result"] != "pass")
                print(f"  {row['lead_id']}: {row['verdict']} {row['score_shown']} - {row['verdict_reason']}"
                      + (f" | {reasons}" if reasons else ""))
        elif command == "import-calls" and len(args) == 4 and args[1] == "--run":
            from lead_qual.inbox import import_calls
            counts = import_calls(store.customer(), args[2], args[3])
            print(f"Calls of {args[2]} {args[3]}: " + ", ".join(f"{v} {k}" for k, v in counts.items())
                  + f". Inbox: {store.INBOX}")
        elif command == "add" and len(args) >= 3:
            from lead_qual.inbox import manual
            fields = parse_options(args[1:])
            result, lead, why = manual(fields.pop("source", ""), fields)
            print({"added": f"Added {lead['lead_id']}.",
                   "already": f"Already in the inbox as {lead['lead_id']} (same ref): nothing changed.",
                   "duplicate": f"Same {why} as {lead['lead_id']}: noted as a duplicate, not added."}[result])
        elif command == "check-inbox" and args[1:] in ([], ["--recheck"]):
            from lead_qual.criteria import approved
            from lead_qual.inbox import check_inbox
            test_mode_only()
            key = store.customer()
            checked = check_inbox(key, approved(key), recheck=args[1:] == ["--recheck"])
            print(inbox_summary(checked))
            for lead in checked:
                c = lead["check"]
                tag = f"{lead['source']}" + (", TEST MODE: no phone" if lead.get("test_mode") else "") + (
                    ", re-checked" if lead.get("previous_checks") else "")
                print(f"  {lead['lead_id']} [{tag}] {lead.get('name') or '-'} @ {lead.get('company') or '-'}: "
                      f"{c['verdict']} {c['score_shown']} - {c['verdict_reason']}")
        elif command == "answer" and len(args) == 4:
            from lead_qual.check import answer
            row = answer(args[1], args[2], args[3])
            print(f"Lead {row['lead_id']}: {row['verdict']} -> {row['human_verdict']} by {row['answered_by']}.")
        else:
            print("Usage:\n" + USAGE)
            sys.exit(1)
    except Exception as error:
        from quickicp import ToolError              # an LLM failure: one STOPPED line, as everywhere in the brain
        if not isinstance(error, (store.Stop, ToolError)):
            raise
        print(error)
        sys.exit(1)
