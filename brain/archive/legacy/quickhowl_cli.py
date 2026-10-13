"""
archive/legacy/quickhowl_cli.py
===============================

The old CLI parts of quickhowl.py (paid / send / file-based commands that skipped the ledger and the
approvals), moved here word for word on 2026-10-13. The run path uses the orchestrator runners instead.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from quickhowl import *          # noqa: F401,F403


# ---- Files ----
PREVIEW_FILE = "data/call_launch_preview.json"


STATE_FILE = "data/call_campaign_state.json"


HISTORY_FILE = "data/call_campaigns.json"


RESULTS_FILE = "data/call_results.json"


def calls_work_order() -> dict:
    """The Quickhowl work order written by the Master Agent."""
    work_orders = load_json("data/work_orders.json")
    for order in work_orders if isinstance(work_orders, list) else work_orders.values():
        if "quickhowl" in str(order.get("agent", "")).lower():
            return order
    raise ToolError("No Quickhowl work order in data/work_orders.json. Check AI calling is enabled "
                    "and run 'python master_agent.py'.")


def passed_linkedin_urls() -> list[tuple[str, int]]:
    """(LinkedIn URL, judge score) of people who passed the judges in the latest run, best first."""
    people = load_json("data/quickicp_phones_raw.json")
    rows = []
    for person in people:
        url = person.get("USER SOCIAL")
        validation = person.get("validation") or {}
        if url and validation.get("status", "pass") == "pass":
            rows.append((url, validation.get("score") or 0))
    rows.sort(key=lambda row: -row[1])
    if not rows:
        raise ToolError("No passed people in data/quickicp_phones_raw.json. Run 'python pipeline.py' first.")
    return rows


def build_contacts() -> list[dict]:
    """
    Contacts for the call campaign. Real prospect details always; the phone is the prospect's
    own in real mode, or one of our TEST_CALL_NUMBERS in test mode.
    """
    ranked = passed_linkedin_urls()
    order = {url: index for index, (url, _) in enumerate(ranked)}
    people = supabase_people([url for url, _ in ranked])
    people.sort(key=lambda p: order.get(p.get("user_social"), 10**6))
    print(f"Passed people in latest run: {len(ranked)} | found in Supabase: {len(people)}")
    return contacts_from_people(people)


def print_request(request: dict) -> None:
    print(f"\nclient_reference: {request['client_reference']}")
    print(f"reference:        {request['reference']}")
    print(f"campaign:         {request['campaign']['name']}")
    print(f"agent:            {request['agent']['name']} ({request['agent']['language']}, "
          f"{request['agent']['voice_provider']})")
    print("\nCompany profile the caller will use:")
    for key, value in request["company_profile"].items():
        print(f"  {key}: {value}")
    print(f"\nContacts ({len(request['contacts'])}):")
    for contact in request["contacts"]:
        role = contact.get("custom_fields", {}).get("designation", "")
        print(f"  {contact.get('name')} | {role} | {contact.get('company')} | phone {contact['phone']}")


def print_launch_reply(reply: dict) -> None:
    for block in ("account", "phone_number", "company_profile", "agent", "campaign"):
        value = reply.get(block)
        if isinstance(value, dict):
            extra = f" - {value['reason']}" if value.get("reason") else ""
            print(f"  {block}: {value.get('status')}{extra}")
        elif value is None and block in reply:
            print(f"  {block}: none")
    if reply.get("contacts_rejected"):
        print(f"  contacts_rejected: {reply['contacts_rejected']}")
    print(f"  duplicate: {reply.get('duplicate')} | start_status: {reply.get('start_status')}"
          + (f" - {reply['start_reason']}" if reply.get("start_reason") else ""))


def load_state() -> dict:
    state = read_json(STATE_FILE, None)
    if not state or not state.get("reference"):
        raise ToolError("No call campaign yet. Run 'python quickhowl.py launch' first.")
    return state


def save_state(state: dict) -> None:
    save_json(STATE_FILE, state)


def add_history(entry: dict) -> None:
    history = read_json(HISTORY_FILE, [])
    history.append(entry)
    save_json(HISTORY_FILE, history)


def cmd_preview() -> dict:
    request = build_request()
    assert_safe(request)
    save_json(PREVIEW_FILE, request)
    print_request(request)
    print(f"\nSaved {PREVIEW_FILE} (no secrets). Nothing was sent.")
    return request


def cmd_launch() -> None:
    """Store the campaign (auto_start false). Nothing is dialled."""
    request = cmd_preview()
    print("\nLaunching with auto_start=false: the campaign is STORED, nobody is called.")
    reply = call_api("POST", "/launch", body=with_secrets(request, auto_start=False))
    print_launch_reply(reply)

    state = {"client_reference": request["client_reference"], "reference": request["reference"],
             "launched_at": now_iso(), "test_mode": TEST_MODE, "launch_reply": reply,
             "started": False, "next_since": None}
    save_state(state)
    add_history({"client_reference": request["client_reference"], "reference": request["reference"],
                 "launched_at": state["launched_at"], "test_mode": TEST_MODE,
                 "contacts": len(request["contacts"])})

    if reply.get("start_status") == "blocked":
        raise ToolError(f"Launch blocked: {reply.get('start_reason')}. Fix it, then run 'launch' again.")
    print(f"\nStored. Next: python quickhowl.py start   (asks before dialling)")


def cmd_start() -> None:
    """Human approval, then the identical request with auto_start true (guide scenario 7)."""
    state = load_state()
    request = read_json(PREVIEW_FILE, None)
    if not request or request.get("reference") != state["reference"]:
        raise ToolError("The saved request does not match the stored campaign. Run 'launch' again.")
    assert_safe(request)
    print_request(request)
    if TEST_MODE:
        print("\nTEST MODE: only our own test numbers above will ring.")
    print("Calls go out inside the calling window (default 09:00-19:00 Mon-Sat), one at a time, "
          "20-40 s preparation before each. Unanswered calls are not retried.")
    if not ask_yes("\nStart dialling now?"):
        print("Not started.")
        return

    reply = call_api("POST", "/launch", body=with_secrets(request, auto_start=True))
    print_launch_reply(reply)
    state.update({"started": reply.get("start_status") in ("started", "already_started"),
                  "started_at": now_iso(), "start_reply": reply})
    save_state(state)
    if reply.get("start_status") == "blocked":
        raise ToolError(f"Start blocked: {reply.get('start_reason')}")
    print("\nNext: python quickhowl.py watch   (or 'status' / 'results' any time)")


def cmd_status() -> dict:
    state = load_state()
    reply = call_api("GET", f"/campaigns/{state['reference']}", params=campaign_params(state), timeout=60)
    print(f"\nCampaign {state['reference']}")
    print(f"  status: {reply.get('status')} | in calling window now: {reply.get('in_calling_window_now')}")
    print(f"  window: {reply.get('calling_window')}")
    print(f"  contacts: {reply.get('contacts_total')} total, {reply.get('contacts_still_to_call')} still to call")
    print(f"  contacts_by_status: {reply.get('contacts_by_status')}")
    print(f"  calls: {reply.get('calls_total')} | by outcome: {reply.get('calls_by_outcome')}")
    return reply


def cmd_results(quiet: bool = False) -> dict:
    """Fetch new/changed calls since last time (all pages), merge by call_id, save, summarise."""
    state = load_state()
    results = read_json(RESULTS_FILE, {})
    since, fetched, campaign_status = state.get("next_since"), 0, None
    while True:
        params = {**campaign_params(state), "limit": PAGE_LIMIT}
        if since:
            params["since"] = since
        reply = call_api("GET", f"/campaigns/{state['reference']}/calls", params=params, timeout=60)
        for item in reply.get("items", []):
            item["transcript"] = clean_transcript(item.get("transcript"))
            item["reference"] = state["reference"]
            results[item["call_id"]] = item
            fetched += 1
        campaign_status = reply.get("campaign_status")
        since = reply.get("next_since") or since
        if not reply.get("has_more"):
            break

    state["next_since"] = since
    save_state(state)
    save_json(RESULTS_FILE, results)

    mine = [r for r in results.values() if r.get("reference") == state["reference"]]
    if not quiet or fetched:
        print(f"\nCalls updated: {fetched} | campaign: {campaign_status} | calls so far: {len(mine)}")
        for call in mine:
            contact = call.get("contact") or {}
            summary = (call.get("summary") or "").replace("\n", " ")[:120]
            print(f"  {str(call.get('status')):10} {str(call.get('outcome')):18} "
                  f"{str(call.get('duration_seconds') or '-'):>4}s  {contact.get('name')} | "
                  f"{contact.get('company')}  {summary}")
        outcomes = {}
        for call in mine:
            outcomes[call.get("outcome")] = outcomes.get(call.get("outcome"), 0) + 1
        print(f"  Outcomes: {outcomes}")
        print(f"  Saved {RESULTS_FILE}")
    return {"campaign_status": campaign_status, "calls": mine}


def cmd_watch() -> None:
    """Poll every POLL_SECONDS until finished or WATCH_MINUTES pass. Ctrl+C only stops watching."""
    deadline = time.time() + WATCH_MINUTES * 60
    print(f"Watching every {POLL_SECONDS} s for up to {WATCH_MINUTES} min (Ctrl+C stops watching, "
          f"not the calls).")
    try:
        while True:
            outcome = cmd_results(quiet=True)
            status = cmd_status()
            if status.get("status") == "draft":
                # Known issue 3: a draft campaign never starts by itself; stop at once.
                print("\nThe campaign is still a DRAFT (stored, not started): nobody will be called. "
                      "Run 'python quickhowl.py start' first.")
                return
            calls = outcome["calls"]
            all_finished = all(call.get("finished") for call in calls) if calls else False
            if status.get("status") in FINISHED_STATUSES and status.get("contacts_still_to_call") == 0 \
                    and all_finished:
                print("\nCampaign finished.")
                cmd_results()
                return
            if time.time() > deadline:
                print(f"\nStopped watching after {WATCH_MINUTES} min. Calls continue; run 'watch' again.")
                return
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped watching. Calls continue on the server; run 'results' or 'watch' later.")


def cmd_pause() -> None:
    state = load_state()
    if not ask_yes(f"Pause campaign {state['reference']}? Calls in progress finish; no new calls."):
        print("Not paused.")
        return
    reply = call_api("POST", f"/campaigns/{state['reference']}/pause", params=campaign_params(state))
    print(f"  paused: {reply.get('paused')} | status: {reply.get('status')} | {reply.get('reason')}")
    if reply.get("next_action"):
        print(f"  {reply['next_action']}")


COMMANDS = {"preview": cmd_preview, "launch": cmd_launch, "start": cmd_start, "status": cmd_status,
            "results": cmd_results, "watch": cmd_watch, "pause": cmd_pause}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if command not in COMMANDS:
        print("Usage: python quickhowl.py " + " | ".join(COMMANDS))
        sys.exit(1)
    try:
        COMMANDS[command]()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: str, default):
    """Read a JSON file we own; return default if it does not exist yet."""
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} Type 'yes' to continue: ").strip().lower() == "yes"
    except EOFError:
        return False
