"""
archive/legacy/email_tool_cli.py
================================

The old CLI parts of email_tool.py (paid / send / file-based commands that skipped the ledger and the
approvals), moved here word for word on 2026-10-13. The run path uses the orchestrator runners instead.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from email_tool import *          # noqa: F401,F403


ENRICHED_FILE = "data/quickicp_phones_raw.json"


STATE_FILE = "data/email_campaign_state.json"


SAMPLES_FILE = "data/email_samples.json"


def email_work_order() -> dict:
    """The Email Agent work order from data/work_orders.json ({} if there is none)."""
    for work_order in load_json("data/work_orders.json"):
        if work_order.get("agent") == "Email Agent":
            return work_order
    return {}


def validated_people_links() -> list[str]:
    """LinkedIn URLs of people who passed the judge and have an email, from the latest pipeline run."""
    links = []
    for person in load_json(ENRICHED_FILE):
        if person.get("validation", {}).get("status") == "pass" and person.get("email") and person.get("USER SOCIAL"):
            links.append(person["USER SOCIAL"])
    if not links:
        raise ToolError("No validated people with an email. Run: python pipeline.py")
    return links


def build_launch_body(rows: list[dict] | None = None, write=None) -> dict:
    """The full launch request. rows: people to email (default: the latest pipeline run's file)."""
    return build_launch_request(rows, write)[0]


def masked(body: dict) -> dict:
    copy = json.loads(json.dumps(body))
    if "smtp" in copy:
        copy["smtp"]["password"] = "********"
    return copy


def run_launch(send: bool) -> None:
    body = build_launch_body()
    save_json("data/email_launch_preview.json", masked(body))
    print(json.dumps(masked(body), indent=2, ensure_ascii=False))
    print(f"\nRecipients: {len(body['recipients'])} | test mode: {TEST_MODE} | type: HYPER_PERSONALIZED")
    print("Saved data/email_launch_preview.json")
    if not send:
        return

    status, reply = send_launch(body)
    save_json("data/email_launch_result.json", {"status": status, "reply": reply})
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    start = reply.get("start_status")

    save_json(STATE_FILE, {
        "reference": body["reference"],
        "workspace_id": reply["workspace_id"],
        "campaign_id": reply["campaign_id"],
        "campaign_name": body["campaign"]["name"],
        "launched_at": datetime.now().isoformat(timespec="seconds"),
    })
    print(f"\nLAUNCHED as {reply.get('campaign_status')} ({start}). Saved {STATE_FILE}")
    print("Next: python email_tool.py samples")


def run_samples() -> None:
    state = load_json(STATE_FILE)
    print(f"Campaign: {state['campaign_name']} ({state['campaign_id']})")

    deadline = time.time() + SAMPLE_WAIT_SECONDS
    while True:
        status, reply = call_tool("GET", campaign_url(state, "samples"))
        if status >= 400:
            raise ToolError(f"Samples request rejected ({status}): {json.dumps(reply)[:500]}")
        samples_status = reply.get("samples_status")
        if samples_status != "generating" or time.time() > deadline:
            break
        print("  Samples still generating, checking again in 5s...")
        time.sleep(5)

    save_json(SAMPLES_FILE, reply)
    print(f"\nSamples status: {samples_status} | approval: {reply.get('approval_status')}\n")
    for sample in reply.get("samples", []):
        print(f"--- Email {sample.get('email_number')} | state {sample.get('state')} | "
              f"for {sample.get('recipient_first_name')} at {sample.get('recipient_company')} | "
              f"fallback: {sample.get('fallback_used')} | issues: {sample.get('failure_codes')}")
        print(f"Subject: {sample.get('subject')}\n")
        print(plain_text(sample.get("body_html")) + "\n")

    if samples_status == "ready":
        print("Read them carefully. If they are good: python email_tool.py approve")
    else:
        print("Samples are not ready to approve. Run 'samples' again shortly, or check the campaign in the app.")


def run_approve() -> None:
    state = load_json(STATE_FILE)
    print(f"Campaign: {state['campaign_name']} ({state['campaign_id']})")
    answer = input("Approve and start sending? Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("Not approved. Nothing was started.")
        return

    status, reply = call_tool("POST", campaign_url(state, "approve"))
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    if status >= 400:
        code = reply.get("error", {}).get("code") if isinstance(reply.get("error"), dict) else None
        raise ToolError(f"Approve rejected ({status}). " + APPROVE_MESSAGES.get(code, json.dumps(reply)[:300]))

    state["approved_at"] = datetime.now().isoformat(timespec="seconds")
    state["campaign_status"] = reply.get("campaign_status")
    save_json(STATE_FILE, state)
    print(f"\n{reply.get('status', '').upper()}: campaign is {reply.get('campaign_status')}. "
          "Emails are personalized about an hour before each send, inside the schedule.")


MODES = {
    "preview": lambda: run_launch(send=False),
    "launch": lambda: run_launch(send=True),
    "samples": run_samples,
    "approve": run_approve,
}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if mode not in MODES:
        print("Usage: python email_tool.py " + " | ".join(MODES))
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
