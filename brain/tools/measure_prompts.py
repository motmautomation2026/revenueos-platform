"""
tools/measure_prompts.py
========================

How big is every LLM call of 'master_agent.py --intake'? Rebuilds each prompt EXACTLY, with master_agent's own
nodes, on a saved run's data, and counts its tokens. No LLM call, no paid call, nothing written.

WHY
---
Step 4.0 of the cost work: know where the tokens go before changing any prompt. The real token use of a live
run (incl. cached input tokens) is logged separately in data/llm_usage.jsonl by quickicp.UsageLog.

HOW
---
    1. read the run (brain_customer_runs + brain_plan_tasks): its understanding, plan, revenue analysis, work orders
    2. replace master_agent.ask_llm / ask_llm_batch with a FAKE that records each prompt and answers with the
       run's saved result (so every later node gets the same input it got in the real run)
    3. run the nodes in order: read_document -> understand_document -> analyze_revenue -> plan_month (attempt 1)
       -> validate_plan -> plan_month again (a retry round, to size it) -> create_work_orders
    4. count tokens (tiktoken, the encoding of OPENAI_MODEL; o200k_base if unknown): each prompt + the answer
       schema sent with it

READS
-----
    the run in the state store (Supabase), data/intake_document.txt (must be the intake document that run used),
    knowledge/motm_context.md, .env (OPENAI_MODEL, CUSTOMER_KEY, SUPABASE_*)

WRITES
------
    Nothing. Prints one table.

RUN
---
    python tools/measure_prompts.py                                     (default run: anvixa-ai-product-2026-10-r6)
    python tools/measure_prompts.py anvixa-ai-product-2026-10-r5

DEBUGGING
---------
    "KeyError ... with task_id exactly"  -> master_agent's work-order prompt wording changed; update fake_batch
    validation errors in the output        -> only the size of the retry round; the real run may have had none
    a different document than the run used -> check data/intake_source.json (plan id / read_at) against the run
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))    # the repo root

import tiktoken  # noqa: E402

import master_agent as ma  # noqa: E402
from state_store import get_run, get_tasks  # noqa: E402

DEFAULT_RUN = "anvixa-ai-product-2026-10-r6"


def measure(run_key: str) -> dict:
    """{call name: [(prompt tokens, answer schema tokens), ...]} plus the document sizes. No LLM call."""
    try:
        enc = tiktoken.encoding_for_model(os.getenv("OPENAI_MODEL") or "gpt-4.1")
    except KeyError:
        enc = tiktoken.get_encoding("o200k_base")

    def tokens(text: str) -> int:
        return len(enc.encode(text))

    run = get_run(run_key)
    if not run or not run.get("plan") or not run.get("understanding"):
        sys.exit(f"STOPPED: run {run_key} not found, or it has no saved plan / understanding.")
    plan_saved = dict(run["plan"])
    revenue_saved = plan_saved.pop("revenue_analysis", {})
    tasks = get_tasks(run["id"])
    captured = []                                     # (call name, schema, prompt)

    def fake_ask(schema, prompt, name, timeout=None):
        captured.append((name, schema, prompt))
        if schema is ma.Understanding:
            return ma.Understanding.model_validate(run["understanding"])
        if schema is ma.RevenueInputs:
            return ma.RevenueInputs.model_validate({"number_sources": [], **revenue_saved})
        if schema is ma.MonthPlan:
            return ma.MonthPlan.model_validate(plan_saved)
        raise AssertionError(f"unexpected LLM call: {name}")

    def fake_batch(schema, prompts, name, timeout=None, max_concurrency=5):
        by_id = {t["task_id"]: t["work_order"] for t in tasks}
        answers = []
        for prompt in prompts:
            captured.append((name, schema, prompt))
            task_id = prompt.split("with task_id exactly '")[1].split("'")[0]
            answers.append(ma.WorkOrderDetails.model_validate({**by_id[task_id], "task_id": task_id}))
        return answers

    real = (ma.ask_llm, ma.ask_llm_batch)
    ma.ask_llm, ma.ask_llm_batch = fake_ask, fake_batch
    try:
        state = {"document_path": "data/intake_document.txt",
                 "engagement_settings": {"ai_calling_enabled": True, "calling_split": "x", "whatsapp_enabled": False,
                                         "pseo_enabled": False}}
        state.update(ma.read_document(state))
        state.update(ma.understand_document(state))
        state.update(ma.analyze_revenue(state))
        state.update(ma.plan_month(state))                                    # plan, attempt 1
        validation = ma.validate_plan(state)
        state.update({**validation, "plan_errors": validation["plan_errors"] or ["T4 example rule error (retry round)"]})
        state.update(ma.plan_month(state))                                    # a retry round: + the errors
        state.update(ma.create_work_orders(state))
    finally:
        ma.ask_llm, ma.ask_llm_batch = real

    calls = {}
    for name, schema, prompt in captured:
        calls.setdefault(name, []).append((tokens(prompt), tokens(json.dumps(schema.model_json_schema()))))
    return {"encoding": enc.name, "document": tokens(state["document_text"]), "motm": tokens(state["motm_context"]),
            "tasks": len(tasks), "calls": calls}


if __name__ == "__main__":
    run_key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN
    result = measure(run_key)
    print(f"\nRun {run_key} | encoding {result['encoding']} | intake document {result['document']} tokens | "
          f"MOTM context {result['motm']} tokens | tasks {result['tasks']}")
    print(f"{'call':20} {'calls':>5} {'prompt each':>18} {'schema':>7} {'total in':>9}")
    for name, sizes in result["calls"].items():
        prompts = [p for p, _ in sizes]
        each = f"{min(prompts)}" if min(prompts) == max(prompts) else f"{min(prompts)}-{max(prompts)}"
        print(f"{name:20} {len(sizes):>5} {each:>18} {sizes[0][1]:>7} {sum(p + s for p, s in sizes):>9}")
    print("Month plan: attempt 1 + one sized retry round (the real run may have needed none).")
