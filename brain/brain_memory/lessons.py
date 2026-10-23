"""
brain_memory/lessons.py  -  1 LLM call writes at most 8 short lessons from the facts; CODE keeps only lessons that
cite existing fact ids and use no number that is not in the facts they cite.
"""

from pydantic import BaseModel, Field

from brain_memory.facts import numbers_in

MAX_LESSONS = 8


class Lesson(BaseModel):
    text: str = Field(description="One short lesson for planning the next months (max 30 words)")
    fact_ids: list[str] = Field(description="The ids of the facts this lesson is based on, e.g. ['F2', 'F5']")


class Lessons(BaseModel):
    lessons: list[Lesson] = Field(default_factory=list, description=f"At most {MAX_LESSONS} lessons")


PROMPT = ("These are the facts of one month of MOTM's work for a customer. Write at most 8 short lessons that would "
          "help plan the next months better (what worked, what did not, what to change). Every lesson must cite "
          "the ids of the facts it is based on, and may only use numbers that appear in those facts. No advice "
          "that the facts do not support.\n\nFACTS:\n")
TEST_NOTE = ("This was a TEST run: its volumes were test caps, not results (those facts carry no numbers). Write no "
             "lesson about volumes, targets or shortfalls; only about what the outcomes, reviews and notes show.\n\n")


def check(found: Lessons, facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept lessons, dropped lessons with the reason). Code decides."""
    by_id = {f["id"]: f for f in facts}
    kept, dropped = [], []
    for lesson in found.lessons:
        ids = [i.strip() for i in lesson.fact_ids]
        unknown = [i for i in ids if i not in by_id]
        allowed = {n for i in ids if i in by_id for n in by_id[i]["numbers"]}
        invented = [n for n in numbers_in(lesson.text) if n not in allowed]
        reason = ("no fact id" if not ids else f"unknown fact id(s) {unknown}" if unknown
                  else f"number(s) not in the cited facts: {[f'{n:g}' for n in invented]}" if invented
                  else f"more than {MAX_LESSONS} lessons" if len(kept) >= MAX_LESSONS else None)
        if reason:
            dropped.append({"text": lesson.text, "fact_ids": ids, "reason": reason})
        else:
            kept.append({"text": lesson.text.strip(), "fact_ids": ids})
    return kept, dropped


def write(facts: list[dict], ask=None, test: bool = False) -> tuple[list[dict], list[dict]]:
    if ask is None:
        from quickicp import ask_llm as ask
    found = ask(Lessons, (TEST_NOTE if test else "") + PROMPT + "\n".join(f"{f['id']}: {f['text']}" for f in facts), "Memory lessons")
    return check(found, facts)
