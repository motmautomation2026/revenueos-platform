"""
brain_memory/read.py  -  lessons_text(): the ONE function master_agent uses. Approved memory only (brain_decisions
rows, read-only, sha256 checked): the latest approved entry of each of the 3 most recent months, at most about
800 tokens. No approved memory -> "" (then master_agent's prompts are exactly as before).
Entries from TEST runs (test: true) are left out unless allow_test (master_agent: test mode + --allow-test only).
"""

MONTHS = 3
MAX_CHARS = 3200                 # ~800 tokens at ~4 characters per token (no tokenizer download needed)
RULE = "The customer's document and this month's numbers always win over these lessons."


def lessons_text(key: str, reads=None, allow_test: bool = False) -> str:
    """'' when there is nothing approved, or when the memory cannot be read (planning never stops for it)."""
    from brain_memory import store
    try:
        rows = store.approved_rows(key, reads)
    except Exception as error:                                   # e.g. no network: plan without lessons, say so
        print(f"  [memory] lessons not loaded ({type(error).__name__}); planning without them.")
        return ""
    latest = {}
    for entry in rows:                                           # oldest first: a later approval of a month wins
        if allow_test or not entry.get("test"):
            latest[entry["month"]] = entry
    months = sorted(latest, reverse=True)[:MONTHS]
    lines = [f"- [{month}] {lesson['text']}" for month in months for lesson in latest[month]["lessons"]]
    while lines and len("\n".join(lines)) > MAX_CHARS:
        lines.pop()                                              # the oldest month's last lesson goes first
    if not lines:
        return ""
    return ("Lessons from earlier months (facts of those runs, approved by a person):\n" + "\n".join(lines)
            + "\n" + RULE)
