"""
campaign_monitor/cli.py  -  the commands (CUSTOMER_KEY from .env; run from the brain folder or the customer's folder):

    python -m campaign_monitor check [--run <month>]   check every tracked campaign now (FREE tool reads + ledger)
    python -m campaign_monitor status                  what is tracked, the last check, stalls, callbacks (ledger only)
    python -m campaign_monitor selftest                fakes only: no network, no tool, no Supabase

Exit codes of 'check': 0 = every campaign on track, 2 = a NEW stall was raised, 1 = stopped or a read failed.
The last line always starts with 'MONITOR:' (for the worker).
"""

import sys

USAGE = __doc__.split("\n\n", 1)[1].split("\n\nExit", 1)[0].rstrip()


def customer() -> str:
    from state_store import current_customer
    return current_customer()


def check_command(key: str, month: str | None, **fakes) -> int:
    from campaign_monitor.check import check_all
    results = check_all(key, month, **fakes)
    for result in results:
        print(f"  {result.get('line') or result.get('failed') or result.get('skipped')}")
    raised = sum(len(r.get("alerts") or []) for r in results)
    failed = sum(1 for r in results if r.get("failed"))
    stalled = sum(1 for r in results if r.get("stalled_days"))
    print(f"MONITOR: {len(results)} campaign(s) checked, {stalled} stalled, {raised} new alert(s), {failed} read(s) failed")
    return 1 if failed else 2 if raised else 0


def status_command(key: str, reads=None, decisions=None) -> None:
    from campaign_monitor import config
    from campaign_monitor.find import campaigns
    if decisions is None:
        from state_store import get_decisions as decisions
    found = campaigns(key, None, reads)
    if not found:
        print("MONITOR: no launched calls / email campaign in the ledger for this customer.")
        return
    for c in found:
        last = c["last_check"]
        alerts = [d for d in decisions(c["run"]["id"]) if d.get("kind") == config.ALERT_KIND
                  and (d.get("question") or {}).get("campaign") == c["reference"]]
        state = "finished (not tracked)" if not c["tracked"] else "tracked"
        print(f"  {c['run']['run_key']} {c['task']['task_id']} {c['kind']} {c['reference']}: {state}; "
              f"{len(c['monitors'])} check(s), last {last.get('checked_at') or 'never'}: {last.get('summary') or '-'}")
        for d in alerts:
            print(f"      stalled {(d.get('question') or {}).get('day')}: {d.get('reason')} (decision #{d['id']})")
        for cb in last.get("callbacks") or []:
            print(f"      callback requested {cb.get('day')}: {cb.get('name')} @ {cb.get('company')} (call {cb.get('call_id')})")
    print(f"MONITOR: {sum(c['tracked'] for c in found)} tracked, {sum(not c['tracked'] for c in found)} finished")


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    try:
        if command == "selftest" and len(args) == 1:
            from campaign_monitor.selftest import selftest
            sys.exit(0 if selftest() else 1)
        elif command == "check" and (len(args) == 1 or (len(args) == 3 and args[1] == "--run")):
            month = args[2] if len(args) == 3 else None
            if month:
                from orch.common import check_month
                check_month(month)
            sys.exit(check_command(customer(), month))
        elif command == "status" and len(args) == 1:
            status_command(customer())
        else:
            print("Usage:\n" + USAGE)
            sys.exit(1)
    except Exception as error:
        from quickicp import ToolError
        if not isinstance(error, ToolError):
            raise
        print(error)
        print("MONITOR: stopped")
        sys.exit(1)
