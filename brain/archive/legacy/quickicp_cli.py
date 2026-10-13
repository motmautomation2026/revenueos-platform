"""
archive/legacy/quickicp_cli.py
==============================

The old CLI parts of quickicp.py (paid / send / file-based commands that skipped the ledger and the
approvals), moved here word for word on 2026-10-13. The run path uses the orchestrator runners instead.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from quickicp import *          # noqa: F401,F403


TEST_START = 3                  # which company to start from in the standalone people test


def load_quickicp_work_order(output_type: str) -> dict:
    """Find the QuickICP work order for 'accounts' or 'contacts' in the Master Agent output."""
    for work_order in load_json("data/work_orders.json"):
        if work_order.get("agent") == "QuickICP" and work_order.get("output_type") == output_type:
            return work_order
    raise ToolError(f"No QuickICP work order with output_type '{output_type}' in data/work_orders.json.")


def run_companies() -> None:
    check_settings("OPENAI_MODEL", "PROSPEO_API_KEY", "QUICKICP_WEBHOOK_URL")
    understanding = load_json("data/understanding.json")
    work_order = load_quickicp_work_order("accounts")
    print("Work order:", work_order.get("work_order_id"), "|", work_order.get("target"), "|",
          work_order.get("geography"))

    payload = build_company_payload(work_order, understanding)
    print("\n[4] Payload:", json.dumps(payload.model_dump(), indent=2, ensure_ascii=False))
    save_json("data/quickicp_payload.json", payload.model_dump())

    companies = search_companies(payload)
    save_json("data/quickicp_raw.json", companies)
    print(f"\n[5] Companies received: {len(companies)} -> saved to data/quickicp_raw.json")


def load_domains(companies_path: str = "data/quickicp_raw.json") -> list[str]:
    """Cleaned, de-duplicated website domains from a companies file."""
    domains = []
    for company in load_json(companies_path):
        domain = clean_domain(company.get("WEBSITE"))
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def build_people_payloads(work_order: dict, domains: list[str]) -> list[PeoplePayload]:
    """Split domains into batches, each with the same job titles and per-company count."""
    job_titles = make_job_titles(work_order)
    print("\n[2] Job titles:", job_titles)

    contacts_wanted = max_number(work_order.get("target", ""), len(domains))
    per_company = min(10, max(1, math.ceil(contacts_wanted / len(domains))))
    if TEST_MODE:
        per_company = TEST_PEOPLE_PER_COMPANY
    print(f"[3] Contacts wanted: {contacts_wanted} for {len(domains)} companies -> {per_company} per company")

    batches = [domains[i:i + DOMAIN_BATCH_SIZE] for i in range(0, len(domains), DOMAIN_BATCH_SIZE)]
    return [PeoplePayload(domains=batch, job_titles=job_titles, locations=PEOPLE_LOCATIONS,
                          count_per_company=per_company) for batch in batches]


def run_people(companies_path: str = "data/quickicp_raw.json", test_start: int = TEST_START) -> None:
    """
    People search on the companies in companies_path.
    The pipeline passes data/companies_passed.json, so only validated companies are searched.
    """
    check_settings("OPENAI_MODEL", "QUICKICP_PEOPLE_WEBHOOK_URL")
    work_order = load_quickicp_work_order("contacts")
    print("Work order:", work_order.get("work_order_id"), "|", work_order.get("target"))

    domains = load_domains(companies_path)
    if TEST_MODE:
        domains = domains[test_start:test_start + TEST_MAX_PEOPLE_COMPANIES]
    print(f"\n[1] Domains to search (from {companies_path}): {len(domains)}")
    if not domains:
        raise ToolError(f"No domains to search in {companies_path}. Run the company step first.")

    payloads = build_people_payloads(work_order, domains)
    print(f"[4] Sending {len(payloads)} batch(es) of up to {DOMAIN_BATCH_SIZE} domains")

    people, failed = [], []
    for number, payload in enumerate(payloads, start=1):
        try:
            batch_people = search_people(payload)
            people.extend(batch_people)
            print(f"  Batch {number}: {len(batch_people)} people")
        except ToolError as error:
            failed.append({"batch": number, "domains": payload.domains, "error": str(error)})
            print(f"  Batch {number}: FAILED -> {error}")
        save_json("data/quickicp_people_raw.json", people)

    save_json("data/quickicp_people_failed.json", failed)
    print(f"\n[5] People received: {len(people)} | failed batches: {len(failed)}")
    print("Saved to data/quickicp_people_raw.json")
    if people:
        print("First record:", json.dumps(people[0], indent=2, ensure_ascii=False))


def run_enrichment(name: str, webhook: str, input_path: str, output_path: str, failed_path: str) -> None:
    """
    Read people, send them in batches, save results after every batch, record failed batches.
    Our own 'validation' block is removed before sending (the tool expects its own format)
    and attached again to each returned record, matched by LinkedIn profile URL.
    """
    people = load_json(input_path)
    if not people:
        raise ToolError(f"No people found in {input_path}. Run the previous step first.")
    if TEST_MODE:
        people = people[:TEST_MAX_EMAIL_PEOPLE]
    print(f"[1] {name}: {len(people)} people from {input_path}")

    validations = {p.get("USER SOCIAL"): p["validation"] for p in people if "validation" in p}
    to_send = [{k: v for k, v in p.items() if k != "validation"} for p in people]

    batches = [to_send[i:i + PEOPLE_BATCH_SIZE] for i in range(0, len(to_send), PEOPLE_BATCH_SIZE)]
    print(f"[2] Sending {len(batches)} batch(es) of up to {PEOPLE_BATCH_SIZE} people")

    results, failed = [], []
    for number, batch in enumerate(batches, start=1):
        try:
            batch_results = enrich_people(name, webhook, batch)
            for record in batch_results:
                if record.get("USER SOCIAL") in validations:
                    record["validation"] = validations[record["USER SOCIAL"]]
            results.extend(batch_results)
            print(f"  Batch {number}: {len(batch_results)} records")
        except ToolError as error:
            failed.append({"batch": number, "people": [p.get("FULL NAME") for p in batch], "error": str(error)})
            print(f"  Batch {number}: FAILED -> {error}")
        save_json(output_path, results)

    save_json(failed_path, failed)
    print(f"\n[3] Records received: {len(results)} | failed batches: {len(failed)}")
    print("Saved to", output_path)
    if results:
        print("First record:", json.dumps(results[0], indent=2, ensure_ascii=False))


def run_emails(input_path: str = "data/quickicp_people_raw.json") -> None:
    """Email finder. The pipeline passes data/people_passed.json, so only validated people are enriched."""
    check_settings("QUICKICP_EMAIL_WEBHOOK_URL")
    run_enrichment("Email finder", EMAIL_WEBHOOK, input_path,
                   "data/quickicp_emails_raw.json", "data/quickicp_emails_failed.json")


def run_phones() -> None:
    """Phone finder on the email results, so each record ends with person + email + phone."""
    check_settings("QUICKICP_PHONE_WEBHOOK_URL")
    run_enrichment("Phone finder", PHONE_WEBHOOK,
                   "data/quickicp_emails_raw.json", "data/quickicp_phones_raw.json",
                   "data/quickicp_phones_failed.json")


def run_all() -> None:
    """Run companies -> people -> emails -> phones without judges, then save to Supabase.
    For the validated flow, use: python pipeline.py"""
    print("\n===== 1/5 COMPANIES =====")
    run_companies()
    print("\n===== 2/5 PEOPLE =====")
    run_people()
    print("\n===== 3/5 EMAILS =====")
    run_emails()
    print("\n===== 4/5 PHONES =====")
    run_phones()

    print("\n===== 5/5 SAVE TO SUPABASE =====")
    check_settings("SUPABASE_URL", "SUPABASE_KEY")
    try:
        # Imported here, not at the top: supabase_store imports from this file (avoids a circular import).
        from supabase_store import current_customer, save_companies, save_people, COMPANIES_FILE, PEOPLE_FILE
        customer = current_customer()   # fixed CUSTOMER_KEY from .env
        lookups = save_companies(load_json(COMPANIES_FILE), customer)
        save_people(load_json(PEOPLE_FILE), lookups, customer)
    except ToolError:
        raise
    except Exception as error:
        print(f"Supabase save failed: {error}")
        print("All results are still safe in the data/ JSON files. Fix the issue and run: python supabase_store.py")


MODES = {
    "companies": run_companies,
    "people": run_people,
    "emails": run_emails,
    "phones": run_phones,
    "all": run_all,
}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "companies"
    if mode not in MODES:
        print("Usage: python quickicp.py " + " | ".join(MODES))
        sys.exit(1)
    try:
        MODES[mode]()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)
