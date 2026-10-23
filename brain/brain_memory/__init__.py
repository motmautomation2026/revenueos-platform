"""
brain_memory/  -  what the brain learned from earlier months (brain logic only). CODE collects the facts of a
run, the LLM writes short lessons that cite them, code drops lessons that invent numbers, a person approves them.
Only master_agent imports it, and only lessons_text(). See README.md. Run: python -m brain_memory build | approve | show | selftest
"""

from brain_memory.read import lessons_text  # noqa: F401  (the one function master_agent uses)
