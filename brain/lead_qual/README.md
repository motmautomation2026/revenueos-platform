# Lead Qualification Agent (`lead_qual/`)

Can this customer actually supply what a lead asks for? The LLM only **reads** (the customer's document once,
each lead once); **code** compares and decides. Brain logic only: no runner, orchestrator or platform part
uses it yet, and nothing is written to Supabase. Test mode only.

## Commands (from the brain folder; they read `./data`)

```
python -m lead_qual criteria                                        draft criteria from data/intake_document.txt (1 LLM call)
python -m lead_qual approve                                         a person approves (typed yes + name)
python -m lead_qual check <leads.json>                              a verdict per lead (at most 1 LLM call per lead)
python -m lead_qual answer <lead_id> verified|not_verified "note"   a person decides a lead, once
python -m lead_qual selftest                                        fake LLM, temp folder: no network, no LLM
```

## Files

| File | What |
| --- | --- |
| `data/intake_document.txt` | the customer's document (from `intake_adapter.py`), read only |
| `data/maps/<customer_key>_lead_criteria.json` | the criteria: `draft` -> `approved` (approved_by, approved_at, sha256) |
| `data/lead_verdicts.json` | the last check; an older one is **moved** to `data/archive/<time>-leads/` first |

## Criteria (built once per customer, approved by a person)

Only **hard restrictions** (e.g. "India only"); sales priorities (priority regions / segments) are not criteria.
Products with their specs (only things a **lead** can state, e.g. the platform it uses, never product features),
delivery (states / regions served and not served, or pan-India), industries (allowed / excluded), company size
(min/max employees, or excluded sizes such as "small and medium"), order size (the LLM copies the text, e.g.
"₹30 lakh to ₹2 crore"; **code** turns lakh / crore / ₹ into INR, or reads e.g. "5 to 500 tonnes"), and questions
about lead fit only. **Every item needs 1-3 word-for-word pieces of the document** (₹ / Rs / Rs. / INR / '�' count as
the same mark; list markers in front such as `-`, `->`, `1.`, `Q-12 ... ->` and `. , ;` at the end are removed first).
Each piece is checked on its own: a wrong piece is dropped, the item is kept while one piece matches, and every value
(industry name, excluded size, order text) must appear in a matched piece, else that value is dropped. An excluded
"industry" that is a company size (e.g. "small and medium-scale companies") is moved to `company_size` by code.
Everything dropped is listed under `dropped` with the reason.

Questions come in two lists: `questions_for_cam` (gaps in the criteria; **approve is refused** until they are answered
in the file and removed) and `lead_questions` (to ask leads later; they never block).
A person may edit the file before approving; an edited or added item says `"source": "cam"`.
`check` refuses criteria that are not approved, or were changed after approval (sha256).

## Leads

```json
[{"lead_id": "L1", "source": "web", "text": "...", "company": "...", "industry": "...", "state": "...",
  "city": "...", "country": "...", "product": "...", "quantity": "500 tonnes", "employees": "1200", "company_size": "large enterprise"}]
```

`lead_id`, `source` and `text` are required. Given fields **win**; the LLM only fills the missing ones (product,
spec values, delivery state, industry, quantity), each with a quote from the lead's text; a value whose quote is not
in the text is thrown away. 0 LLM calls for a lead that gives everything the criteria need.

## Lead inbox (until the CRM is connected)

`data/leads_inbox.json`, one format for every source: `lead_id` (LQ-0001 ...), `source` (call / telecalling / email /
platform / manual), `source_ref`, `received_at`, `name`, `company`, `phone`, `email`, `state`, `city`, `text`, `given`
fields, `test_mode`, `duplicates`, `check`. All storage goes through `store.py` (`read_inbox` / `save_inbox`).

```
python -m lead_qual import-calls --run <month> <task>     interested / callback_requested calls, from the LEDGER
python -m lead_qual add --source telecalling|manual --name .. --company .. --phone .. --text "..."
python -m lead_qual check-inbox [--recheck]              checks only leads not checked yet; --recheck also re-checks
                                                          leads checked against older criteria with no human answer
                                                          (the old check is kept under previous_checks)
```

- Calls: the task's latest done `poll` row in `brain_tool_calls` (no Quickhowl call); text = summary + transcript.
  In a **test-mode** run the phone is ours, not the prospect's: the lead is marked `test_mode` and has no phone.
- De-dup: the same `source` + `source_ref` -> nothing changes; else the same phone (last 10 digits), then email, then
  company + name -> recorded under the existing lead's `duplicates` (its fields and its check stay).
- `answer` also works on inbox leads (saved in the lead's `check`).
- Email and platform sources: designed, not built (the email tool's reply results; the platform's enquiries, read only).

## Verdict (code)

Products are matched like industries: the LLM picks one of the criteria's product names or `other: <what it is>`;
a name -> pass (also an optional CAM `aliases` list per product, e.g. "browser automation": an alias -> pass;
aliases are part of the sha256), `other` or no product -> `unknown`; only a product in the optional `not_offered` list -> `fail`.
Delivery: a country other than India -> `fail`.

Per criterion `pass` / `fail` / `unknown`. Each criterion has a **weight** and a **hard** flag (`criteria.weights`; the
LLM proposes them, the CAM may edit them; they are part of the approved sha256). **Score % = earned / total x 100**
(pass = full weight, unknown = half, fail = 0). Verdict (thresholds in `config.py`): a failed **hard** criterion ->
`not_verified` whatever the score; score >= 80 -> `verified`; 60 to under 80 -> `needs_human`; under 60 ->
`not_verified` only if a criterion failed (only unknowns -> `needs_human`); an unknown **hard** criterion -> at most `needs_human`. Each lead shows its score and the reason.
Criteria approved before scoring (no weights) must be approved again. A `not_verified` or `needs_human` lead can be answered once by a person
(`answer`, note required): `human_verdict`, `human_note`, `answered_by` (PLAN_APPROVER or 'user'), `answered_at`.

Code files: `criteria.py` (build / approve), `extract.py` (LLM reads a lead), `check.py` (compare, verdict, answer),
`regions.py` (Indian state -> region), `config.py` (score thresholds), `inbox.py` (the lead inbox), `store.py` (files), `cli.py` (commands), `selftest.py`.
