"""
campaign_monitor/expect.py  -  CODE decides, per day (India time), what a campaign should have done and what it did.
No I/O. A day is judged only after its sending window has closed. A stall = a day with expected activity but none.

Calls (Quickhowl): activity is expected on a calling day (the tool's window, else 09:00-19:00 Mon-Sat) unless the
    campaign had run out of work by then (finished, or nothing left to call), or it is paused.
Email: step 1 is due on the launch day; step k on (the day step k-1 was sent + its wait_days), moved to the next
    sending weekday (the schedule's weekdays, Mon-Fri). Bounced / replied / unsubscribed people leave the sequence.
    Expected that day = the people due (with yesterday's carry-over), at most daily_limit.
"""

import datetime
import re
from collections import Counter

from campaign_monitor.config import CALL_WINDOW, LAUNCH_DAY_MIN_HOURS

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))   # India: one offset all year (no tzdata needed)
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CALLS_FINISHED = {"completed", "failed", "paused"}                 # as quickhowl.FINISHED_STATUSES
EMAIL_FINISHED = {"COMPLETED", "FINISHED", "STOPPED", "CANCELLED"}
STOPS = ("bounced", "replied", "unsubscribed")
SCHEDULED_KEYS = ("callbacks_scheduled", "scheduled_callbacks", "retries_scheduled")


def to_ist(stamp) -> datetime.datetime:
    if isinstance(stamp, datetime.datetime):
        return stamp.astimezone(IST)
    return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(IST)


def clock(text: str) -> datetime.time:
    hours, minutes = (int(x) for x in text.split(":")[:2])
    return datetime.time(hours, minutes)


def parse_window(text: str | None) -> tuple[datetime.time, datetime.time, set[int]]:
    """'09:00-19:00 Mon-Sat' -> (09:00, 19:00, {0..5}). Anything unreadable -> the default window."""
    times = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", (text or "").lower())
    text = (text or "").lower() if times else CALL_WINDOW.lower()
    times = times or re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", text)
    days = re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s*-\s*(mon|tue|wed|thu|fri|sat|sun)", text)
    first, last = (DAY_NAMES.index(days[1]), DAY_NAMES.index(days[2])) if days else (0, 5)
    return clock(times[1]), clock(times[2]), set(range(first, last + 1)) if first <= last else set(range(first, 7)) | set(range(last + 1))


def judged_days(launched: datetime.datetime, now: datetime.datetime, closes: datetime.time):
    """Every day from the launch day whose window has closed by now."""
    day = launched.date()
    while datetime.datetime.combine(day, closes, IST) <= now:
        yield day
        day += datetime.timedelta(days=1)


def late_launch(day: datetime.date, launched: datetime.datetime, closes: datetime.time) -> bool:
    return day == launched.date() and datetime.datetime.combine(day, closes, IST) - launched < datetime.timedelta(
        hours=LAUNCH_DAY_MIN_HOURS)


def call_time(call: dict):
    stamp = call.get("started_at") or call.get("created_at") or call.get("ended_at") or call.get("updated_at")
    return to_ist(stamp) if stamp else None


def calls_finished(status: dict, calls: list[dict]) -> bool:
    """As the calls runner: a finished status, nobody still to call, every call finished; and nothing scheduled."""
    return (status.get("status") in CALLS_FINISHED and status.get("contacts_still_to_call") == 0
            and bool(calls) and all(c.get("finished") for c in calls)
            and not any(status.get(k) for k in SCHEDULED_KEYS))


def calls_days(launched_at, now, status: dict, calls: list[dict]) -> dict:
    launched, now = to_ist(launched_at), to_ist(now)
    opens, closes, weekdays = parse_window(status.get("calling_window"))
    by_day = Counter(t.date() for t in map(call_time, calls) if t)
    last_call = max(by_day, default=None)
    finished = calls_finished(status, calls)
    left = status.get("contacts_still_to_call")
    work_left = (left is None or left > 0) or any(status.get(k) for k in SCHEDULED_KEYS)
    limit = status.get("daily_limit")
    days = []
    for day in judged_days(launched, now, closes):
        after_last = last_call is None or day > last_call
        if day.weekday() not in weekdays:
            why = "not a calling day"
        elif late_launch(day, launched, closes):
            why = "launched late that day"
        elif after_last and (finished or not work_left):
            why = "nothing left to call"
        elif after_last and status.get("status") == "paused":
            why = "paused"
        else:
            why = "calls expected"
        expected = why == "calls expected"
        actual = by_day.get(day, 0)
        days.append({"day": day.isoformat(), "expected": expected, "actual": actual,
                     "expected_count": (min(limit, left) if limit and left else left) if expected else 0,
                     "stall": expected and actual == 0, "why": why if not (expected and actual == 0) else
                     f"calls expected ({left} contact(s) left, window {opens:%H:%M}-{closes:%H:%M}) but none made"})
    callbacks = [{"call_id": c.get("call_id"), "name": (c.get("contact") or {}).get("name"),
                  "company": (c.get("contact") or {}).get("company"), "summary": (c.get("summary") or "")[:300],
                  "day": call_time(c).date().isoformat() if call_time(c) else None}
                 for c in calls if c.get("outcome") == "callback_requested"]
    return {"days": days, "finished": finished, "callbacks": callbacks,
            "summary": f"{status.get('status')}, {len(calls)} call(s), {left} left"}


def next_send_day(day: datetime.date, weekdays: set[int]) -> datetime.date:
    while day.weekday() not in weekdays:
        day += datetime.timedelta(days=1)
    return day


def email_days(launched_at, now, schedule: dict, waits: list[int], recipients: list[str], results: list[dict],
               status: str | None = None) -> dict:
    """results: one row per recipient per step sent: {recipient, step (1..), sent_at, delivered, bounced, opened,
    replied, unsubscribed}. shortcut: a stop (bounce / reply / unsubscribe) counts from the day that step was sent;
    use the event times once the real results endpoint gives them."""
    launched, now = to_ist(launched_at), to_ist(now)
    weekdays = {int(d) - 1 for d in schedule.get("weekdays") or [1, 2, 3, 4, 5]}       # ISO 1 = Monday
    closes = clock(schedule.get("window_end_local") or "17:30")
    limit = schedule.get("daily_limit")
    sent, stopped = {}, {}
    for row in results:
        day = to_ist(row["sent_at"]).date()
        sent[(row["recipient"], int(row["step"]))] = day
        if any(row.get(k) for k in STOPS):
            stopped[row["recipient"]] = min(day, stopped.get(row["recipient"], day))
    first = next_send_day(launched.date() + datetime.timedelta(days=1) if late_launch(launched.date(), launched, closes)
                          else launched.date(), weekdays)

    def due(person, day):
        if person in stopped and stopped[person] < day:
            return False
        step = 1 + sum(1 for k in range(1, len(waits) + 1) if sent.get((person, k)) and sent[(person, k)] < day)
        if step > len(waits) or (person, step) in sent and sent[(person, step)] <= day:
            return False
        if step == 1:
            return first <= day
        return next_send_day(sent[(person, step - 1)] + datetime.timedelta(days=int(waits[step - 1] or 0)), weekdays) <= day

    days = []
    for day in judged_days(launched, now, closes):
        actual = sum(1 for d in sent.values() if d == day)
        if day.weekday() not in weekdays:
            expected, why = 0, "not a sending day"
        else:
            due_now = sum(1 for p in recipients if due(p, day) or sent_on(sent, p, day))
            expected = min(due_now, limit) if limit else due_now
            why = "emails expected" if expected else "nothing due"
        stall = expected > 0 and actual == 0
        days.append({"day": day.isoformat(), "expected": expected > 0, "actual": actual, "expected_count": expected,
                     "stall": stall, "why": f"{expected} email(s) due but none sent" if stall else why})
    done = all(p in stopped or (p, len(waits)) in sent for p in recipients)
    steps = [{"step": k, **{field: sum(1 for r in results if int(r["step"]) == k and (field == "sent" or r.get(field)))
                            for field in ("sent", "delivered", "bounced", "opened", "replied", "unsubscribed")}}
             for k in range(1, len(waits) + 1)]
    return {"days": days, "finished": done and (status is None or str(status).upper() in EMAIL_FINISHED),
            "callbacks": [], "steps": steps,
            "summary": f"{status or 'status n/a'}, {len(results)} email(s) sent to {len(recipients)} people"}


def sent_on(sent: dict, person: str, day: datetime.date) -> bool:
    """Someone sent ON this day was due on it (so a day's sends never look like 'more than expected')."""
    return any(p == person and d == day for (p, _), d in sent.items())
