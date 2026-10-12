"""
supabase_store.py
=================

Saves QuickICP results into Supabase, PER CUSTOMER, so they can be checked in real time
and one customer's data never mixes with another's.

FLOW
----
    data/companies_passed.json      (companies that PASSED the company judge; same file as pipeline.py save)
            │
            ▼
    save_companies(companies, customer)  ──► table: quickicp_revenueos_client_company_data
            │
            │  returns lookups for THIS customer: domain / linkedin / name  ->  company_id
            ▼
    data/quickicp_phones_raw.json   (people + email + phone)
            │
            ▼
    save_people(people, lookups, customer) ──► table: quickicp_revenueos_client_people_data
                          (each person linked to their company via company_id)

CUSTOMER
--------
Every row has a 'customer' column (e.g. 'anvixa-ai-product'). The value is the FIXED key
CUSTOMER_KEY from .env, read by state_store.current_customer(); it is never derived from the
customer's display name, so a rename never splits one customer's data in two.
    - every new row is saved with the customer
    - duplicate checks only look at rows of the SAME customer
    - people are only linked to companies of the SAME customer
The same company or person may therefore exist once PER customer (the database enforces this:
unique (customer, website) and unique (customer, user_social)).

ORDER MATTERS
-------------
Companies must be saved first, because each person row needs the `id`
of its company row (people.company_id -> company.id).

HOW A PERSON IS LINKED TO A COMPANY
-----------------------------------
find_company_id() tries three ways, in this order, and stops at the first match:
    1. website domain        person "COMPANY URL"          vs company "website"
    2. company LinkedIn page person "COMPANY SOCIAL LINK"  vs company "linkedin"
    3. company name          person "COMPANY NAME"         vs company "company_name"
Why three: the same company can use different websites
(e.g. Maruti Suzuki: maruti.co.in for the company, marutisuzuki.com for its people).
The lookups it searches only contain the current customer's companies.

DUPLICATES
----------
    - companies are matched by cleaned website domain (e.g. "cipla.com") within the customer
    - people are matched by LinkedIn profile URL (USER SOCIAL) within the customer
Running the script twice inserts nothing new the second time. If a duplicate ever slipped
through, the database's unique rules would refuse the insert (an error, never a silent copy).

REPAIR OF EARLIER ROWS
----------------------
If a person was saved earlier with an EMPTY company_id, email or phone_number, and a value is now
known (same customer), only those empty fields are filled. A saved value is NEVER overwritten.

PHONE TYPE
----------
phone_type(number) -> 'mobile' | 'landline' | 'unknown' (Indian mobiles: 10 digits starting 6-9
after +91). Code rule; the calls runner uses it to skip landlines. Not stored in the table.

HOW TO RUN
----------
    python supabase_store.py                   save data/companies_passed.json + data/quickicp_phones_raw.json
                                               for CUSTOMER_KEY (same as: python pipeline.py save).
                                               Only PASSED companies are saved. No API credits are used.
    python supabase_store.py check             READ-ONLY: how many companies/people in the pipeline files
                                               (companies_passed.json, quickicp_phones_raw.json) are new
                                               vs already saved for this customer. Writes nothing.
    python supabase_store.py separation-test   writes 1 company + 1 person for a 'selftest-<time>' customer,
                                               checks the real customer never sees them (also through
                                               email_tool.py and quickhowl.py), then deletes them by id.

REQUIRED IN .env
----------------
    SUPABASE_URL=https://<project-id>.supabase.co
    SUPABASE_KEY=<service_role key>     # full access: never share or commit
    CUSTOMER_KEY=anvixa-ai-product      # fixed per customer; never change it for an existing customer

COMMON PROBLEMS (debugging guide)
---------------------------------
    "Invalid API key" / 401             -> SUPABASE_KEY wrong or missing in .env
    "relation ... does not exist"       -> table name typo, or table not created yet
    "column ... customer does not exist"-> sql/002_proposal_quickicp_customer.sql not run
    "STOPPED: CUSTOMER_KEY is missing"  -> add CUSTOMER_KEY=<key> to .env
    "duplicate key ... customer_website"-> a company with this website is already saved for this
                                           customer but the code did not see it; compare
                                           clean_domain() of both values
    "null value in column company_name" -> a company record has no COMPANY field
    "No company match for ..." printed  -> none of the three ways matched IN THIS CUSTOMER; compare
                                           the printed domain and company name with the company
                                           table, filtered on this customer
    Wrong company linked to a person    -> two companies share a normalised name; check
                                           clean_name() stop words
    Separation test left rows behind    -> delete rows whose customer starts with 'selftest-'
"""

import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

# clean_domain: turns "https://www.Cipla.com/" into "cipla.com"
# load_json:    reads a JSON file from disk
from quickicp import ToolError, clean_domain, load_json
# check_customer_key / current_customer: the fixed customer key (CUSTOMER_KEY in .env)
from state_store import check_customer_key, current_customer

load_dotenv()

# Table names exactly as created in Supabase. Change here if the developer renames them.
COMPANY_TABLE = "quickicp_revenueos_client_company_data"
PEOPLE_TABLE = "quickicp_revenueos_client_people_data"

# Input files written by quickicp.py
COMPANIES_FILE = "data/quickicp_raw.json"             # all companies found; only used by quickicp.py 'all'
PEOPLE_FILE = "data/quickicp_phones_raw.json"
PASSED_COMPANIES_FILE = "data/companies_passed.json"   # passed companies: what this file and pipeline.py save

# Words removed from company names before matching, so
# "Maruti Suzuki India Limited" and "Maruti Suzuki" are treated as the same company.
NAME_STOP_WORDS = {"limited", "ltd", "pvt", "private", "india", "inc", "llp", "the", "co", "company"}

# Separation test rows always use a customer starting with this, and only those are ever deleted.
SELFTEST_PREFIX = "selftest-"

# One connection to the Supabase project, reused by every function below.
db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


# ================= CLEANING HELPERS =================

def clean_linkedin(url: str | None) -> str:
    """
    Normalise a LinkedIn URL so different spellings of the same page match.

    "https://www.linkedin.com/company/Cipla/" -> "linkedin.com/company/cipla"
    Returns "" if the URL is missing.
    """
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)   # remove http:// or https://
    url = re.sub(r"^www\.", "", url)       # remove leading www.
    return url.rstrip("/")                 # remove trailing slash


def clean_name(name: str | None) -> str:
    """
    Normalise a company name so small differences don't block a match.

    "Maruti Suzuki India Limited" -> "maruti suzuki"
    Lowercases, replaces punctuation with spaces, and removes NAME_STOP_WORDS.
    Returns "" if the name is missing.
    """
    if not name:
        return ""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())   # keep only letters, digits, spaces
    return " ".join(word for word in name.split() if word not in NAME_STOP_WORDS)


# ================= COMPANY LOOKUPS =================

def company_lookups(customer: str) -> dict[str, dict[str, int]]:
    """
    Read every saved company OF THIS CUSTOMER and build three lookups to its row id.

    Returns:
        {
            "domain":   {"cipla.com": 12, ...},
            "linkedin": {"linkedin.com/company/cipla": 12, ...},
            "name":     {"cipla": 12, ...},
        }

    Used to:
        1. Skip companies that are already saved for this customer (by domain).
        2. Link each person to their company (by domain, LinkedIn or name), within this customer.
    """
    check_customer_key(customer)
    rows = (db.table(COMPANY_TABLE).select("id, website, linkedin, company_name")
              .eq("customer", customer).execute().data)

    lookups = {"domain": {}, "linkedin": {}, "name": {}}
    for row in rows:
        if row["website"]:
            lookups["domain"][clean_domain(row["website"])] = row["id"]
        if row["linkedin"]:
            lookups["linkedin"][clean_linkedin(row["linkedin"])] = row["id"]
        if row["company_name"]:
            lookups["name"][clean_name(row["company_name"])] = row["id"]
    return lookups


def find_company_id(person: dict, lookups: dict[str, dict[str, int]]) -> int | None:
    """
    Find a person's company row id, trying three ways in order:

        1. company website domain   (person "COMPANY URL")
        2. company LinkedIn page    (person "COMPANY SOCIAL LINK")
        3. normalised company name  (person "COMPANY NAME")

    Returns the company id from the first way that matches, or None if none do.
    The lookups come from company_lookups(customer), so only that customer's companies match.
    """
    domain = clean_domain(person.get("COMPANY URL"))
    if domain in lookups["domain"]:
        return lookups["domain"][domain]

    linkedin = clean_linkedin(person.get("COMPANY SOCIAL LINK"))
    if linkedin in lookups["linkedin"]:
        return lookups["linkedin"][linkedin]

    name = clean_name(person.get("COMPANY NAME"))
    if name in lookups["name"]:
        return lookups["name"][name]

    return None


def saved_people(customer: str) -> dict[str, dict]:
    """Everyone already saved FOR THIS CUSTOMER, keyed by LinkedIn profile URL."""
    check_customer_key(customer)
    rows = (db.table(PEOPLE_TABLE).select("id, user_social, company_id, email, phone_number")
              .eq("customer", customer).execute().data)
    return {row["user_social"]: row for row in rows if row["user_social"]}


def phone_type(phone: str | None) -> str:
    """
    'mobile', 'landline' or 'unknown' for an Indian number (code rule, used by the calls runner).
        +91 98113 00501 -> mobile   (10 digits after +91, first digit 6-9)
        +91 11 2614 2451 -> landline (first digit 0-5 after +91, or a number starting with 0)
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        national = digits[2:]
    elif len(digits) == 10:
        national = digits
    elif len(digits) == 11 and digits.startswith("0"):
        return "landline"
    else:
        return "unknown"
    return "mobile" if national[0] in "6789" else "landline"


# ================= SAVE COMPANIES =================

def save_companies(companies: list[dict], customer: str) -> dict[str, dict[str, int]]:
    """
    Insert new companies for this customer into the company table.

    Args:
        companies: raw company records from QuickICP company search
                   (keys: COMPANY, WEBSITE, LOCATION, INDUSTRY, HEADCOUNT, PHONE, LINKEDIN)
        customer:  the customer key, e.g. current_customer() -> 'anvixa-ai-product'

    Skips a company if:
        - it has no website (we can't de-duplicate it reliably without one)
        - its domain is already saved for this customer (earlier run)
        - its domain appeared earlier in this same list (duplicate inside the batch)

    Returns:
        The fresh lookups for this customer (domain / linkedin / name -> company_id),
        including the companies just inserted.
    """
    existing = company_lookups(customer)["domain"]   # domains already saved for this customer
    new_rows = []
    seen = set()                                     # domains already added in this run

    for company in companies:
        domain = clean_domain(company.get("WEBSITE"))
        if not domain or domain in existing or domain in seen:
            continue
        seen.add(domain)

        # Map QuickICP field names (right) to table column names (left).
        new_rows.append({
            "customer": customer,
            "company_name": company.get("COMPANY"),
            "website": domain,                     # stored cleaned, e.g. "cipla.com"
            "location": company.get("LOCATION"),
            "industry": company.get("INDUSTRY"),
            "headcount": company.get("HEADCOUNT"),
            "phone": company.get("PHONE"),
            "linkedin": company.get("LINKEDIN"),
        })

    # One insert call for all new rows (faster than one call per company).
    if new_rows:
        db.table(COMPANY_TABLE).insert(new_rows).execute()

    print(f"Customer {customer}: companies {len(new_rows)} inserted, "
          f"{len(companies) - len(new_rows)} skipped (already saved for this customer, or no website)")

    # Read again so the lookups include the ids Supabase just created.
    return company_lookups(customer)


# ================= SAVE PEOPLE =================

def save_people(people: list[dict], lookups: dict[str, dict[str, int]], customer: str) -> None:
    """
    Insert new people for this customer into the people table, each linked to their company.

    Args:
        people:   records from QuickICP people + email + phone search
                  (keys: FULL NAME, USER SOCIAL, JOB POSITION, COUNTRY, LOCATION,
                   INDUSTRY, COMPANY URL, COMPANY SOCIAL LINK, COMPANY NAME, email, phone)
        lookups:  company lookups returned by save_companies() for the SAME customer
        customer: the customer key, e.g. 'anvixa-ai-product'

    For each person:
        - Not saved yet for this customer -> insert, with company_id from find_company_id()
        - Saved, some fields empty        -> fill ONLY the empty company_id / email / phone_number
        - Saved, nothing to fill          -> skip (a saved value is never overwritten)

    People whose company still can't be found are saved with company_id = None
    and printed, so the mismatch can be checked.
    """
    existing = saved_people(customer)

    new_rows = []
    fixed = 0        # existing people whose missing company_id was filled in
    no_company = 0   # new people saved without a company

    for person in people:
        social = person.get("USER SOCIAL")
        company_id = find_company_id(person, lookups)

        if social in existing:
            saved = existing[social]
            # Repair: fill ONLY empty fields (company link, email, phone); never overwrite a saved value.
            updates = {}
            if saved["company_id"] is None and company_id is not None:
                updates["company_id"] = company_id
            if not saved.get("email") and person.get("email"):
                updates["email"] = person["email"]
            if not saved.get("phone_number") and person.get("phone"):
                updates["phone_number"] = person["phone"]
            if updates:
                (db.table(PEOPLE_TABLE).update(updates)
                   .eq("id", saved["id"]).eq("customer", customer).execute())
                fixed += 1
            continue

        if company_id is None:
            no_company += 1
            # Debug line: shows exactly what failed to match.
            print(f"  No company match for {person.get('FULL NAME')} "
                  f"(domain: '{clean_domain(person.get('COMPANY URL'))}', "
                  f"company: '{person.get('COMPANY NAME')}')")

        # Map QuickICP field names (right) to table column names (left).
        new_rows.append({
            "customer": customer,
            "full_name": person.get("FULL NAME"),
            "user_social": social,
            "job_position": person.get("JOB POSITION"),
            "country": person.get("COUNTRY"),
            "location": person.get("LOCATION"),
            "industry": person.get("INDUSTRY"),
            "company_id": company_id,
            "email": person.get("email"),          # added by the email finder
            "phone_number": person.get("phone"),   # added by the phone finder
        })

    # One insert call for all new rows.
    if new_rows:
        db.table(PEOPLE_TABLE).insert(new_rows).execute()

    print(f"Customer {customer}: people {len(new_rows)} inserted, {fixed} existing filled in (empty fields only), "
          f"{no_company} without a matching company")


# ================= READ-ONLY CHECK =================

def check_save(customer: str) -> None:
    """
    READ-ONLY. For the pipeline's files, show what a save would do for this customer.
    Writes nothing.
    """
    companies = load_json(PASSED_COMPANIES_FILE)
    people = load_json(PEOPLE_FILE)
    lookups = company_lookups(customer)
    saved = saved_people(customer)

    domains = {clean_domain(c.get("WEBSITE")) for c in companies if c.get("WEBSITE")}
    new_domains = domains - set(lookups["domain"])
    new_people = [p for p in people if p.get("USER SOCIAL") not in saved]
    no_link = [p for p in new_people
               if find_company_id(p, lookups) is None and clean_domain(p.get("COMPANY URL")) not in new_domains]

    print(f"Customer: {customer} (from CUSTOMER_KEY)")
    print(f"Already saved for this customer: {len(lookups['domain'])} companies, {len(saved)} people")
    print(f"{PASSED_COMPANIES_FILE}: {len(domains)} companies -> {len(new_domains)} new, "
          f"{len(domains) - len(new_domains)} already saved, "
          f"{len(companies) - len([c for c in companies if c.get('WEBSITE')])} without website (skipped)")
    print(f"{PEOPLE_FILE}: {len(people)} people -> {len(new_people)} new, "
          f"{len(people) - len(new_people)} already saved, {len(no_link)} new without a company match")
    print("Nothing was written.")


# ================= SEPARATION TEST (selftest-* customer only) =================

def expect(label: str, condition: bool) -> None:
    """Print PASS, or stop the test with the label."""
    if not condition:
        raise AssertionError(label)
    print(f"  PASS  {label}")


def delete_selftest_rows(customer: str) -> int:
    """
    Delete every row of a selftest-* customer, by id (people first, then companies).
    Refuses any customer that does not start with 'selftest-'. Returns how many rows were deleted.
    """
    if not customer.startswith(SELFTEST_PREFIX):
        raise ToolError(f"STOPPED: refusing to delete rows of customer '{customer}': only {SELFTEST_PREFIX}*.")
    deleted = 0
    for table in (PEOPLE_TABLE, COMPANY_TABLE):
        for row in db.table(table).select("id").eq("customer", customer).execute().data:
            deleted += len(db.table(table).delete().eq("id", row["id"]).eq("customer", customer).execute().data)
    return deleted


def separation_test() -> bool:
    """
    Save 1 company + 1 person for a 'selftest-<time>' customer, using a website and LinkedIn URL that
    the real customer already has, then check the two customers never see each other's rows
    (here and in email_tool.py / quickhowl.py). The test rows are deleted at the end, even on failure.
    """
    real = current_customer()
    test = SELFTEST_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"Separation test: real customer '{real}', test customer '{test}'")

    real_company = (db.table(COMPANY_TABLE).select("id, website, company_name")
                      .eq("customer", real).neq("website", "").order("id").limit(1).execute().data)
    real_person = (db.table(PEOPLE_TABLE).select("id, user_social, full_name")
                     .eq("customer", real).order("id").limit(1).execute().data)
    if not real_company or not real_person:
        print(f"STOPPED: customer '{real}' needs at least 1 saved company and 1 person for this test.")
        return False
    real_company, real_person = real_company[0], real_person[0]
    website, social = real_company["website"], real_person["user_social"]
    real_before = (len(company_lookups(real)["domain"]), len(saved_people(real)))

    ok = False
    try:
        lookups = save_companies([{"COMPANY": "Selftest Company", "WEBSITE": website}], test)
        test_company_id = lookups["domain"].get(website)
        expect("same website saved once more, for the test customer only",
               test_company_id is not None and test_company_id != real_company["id"])

        save_people([{"FULL NAME": "Selftest Person", "USER SOCIAL": social, "COMPANY URL": website}], lookups, test)
        test_person = saved_people(test).get(social)
        expect("same person saved once more, for the test customer only",
               test_person is not None and test_person["id"] != real_person["id"])
        expect("test person linked to the TEST customer's company", test_person["company_id"] == test_company_id)

        expect("saving again inserts nothing (duplicates checked within the customer)",
               save_companies([{"COMPANY": "Selftest Company", "WEBSITE": website}], test)["domain"] == lookups["domain"]
               and len(saved_people(test)) == 1)
        save_people([{"FULL NAME": "Selftest Person", "USER SOCIAL": social, "COMPANY URL": website}], lookups, test)
        expect("people count for test customer still 1", len(saved_people(test)) == 1)

        expect("real customer still links the website to its OWN company",
               company_lookups(real)["domain"].get(website) == real_company["id"])
        expect("real customer's counts unchanged",
               (len(company_lookups(real)["domain"]), len(saved_people(real))) == real_before)
        expect("test customer sees only its own rows",
               len(company_lookups(test)["domain"]) == 1 and len(saved_people(test)) == 1)

        # The two readers used by the tools (no tool is called, only their Supabase reads).
        from email_tool import fetch_people
        from quickhowl import supabase_people
        email_rows = fetch_people([social])
        expect("email_tool.fetch_people returns only the real customer's person",
               len(email_rows) == 1 and email_rows[0]["id"] == real_person["id"]
               and email_rows[0]["company"].get("customer") in (None, real))
        call_rows = supabase_people([social])
        expect("quickhowl.supabase_people returns only the real customer's person",
               len(call_rows) == 1 and call_rows[0]["id"] == real_person["id"]
               and call_rows[0]["company"].get("customer") in (None, real))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        deleted = delete_selftest_rows(test)
        left = (len(db.table(COMPANY_TABLE).select("id").eq("customer", test).execute().data)
                + len(db.table(PEOPLE_TABLE).select("id").eq("customer", test).execute().data))
        print(f"  {'PASS' if left == 0 else 'FAIL'}  cleanup: {deleted} test rows deleted by id, {left} left")

    passed = ok and left == 0
    print("SEPARATION TEST OK" if passed else "SEPARATION TEST FAILED")
    return passed


# ================= RUN =================

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "save"
    try:
        customer = current_customer()
        if command == "save":
            # Step 1: PASSED companies first (same as pipeline.py save), so people can be linked to them.
            lookups = save_companies(load_json(PASSED_COMPANIES_FILE), customer)
            # Step 2: people, linked to this customer's companies by domain, LinkedIn or name.
            save_people(load_json(PEOPLE_FILE), lookups, customer)
        elif command == "check":
            check_save(customer)
        elif command == "separation-test":
            sys.exit(0 if separation_test() else 1)
        else:
            print("Usage: python supabase_store.py [save | check | separation-test]")
            sys.exit(1)
    except ToolError as error:
        print(error)
        sys.exit(1)
