"""
brain_jobs/store.py  -  the job queue in Supabase (sql/004_brain_jobs.sql). All storage goes through this class;
the selftest uses a fake with the same methods and the same rules.

    enqueue(row)                       insert; the same idempotency_key again does nothing -> False
    claim(customer, owner, s, n)       brain_claim_jobs(): atomic, FOR UPDATE SKIP LOCKED (two workers never share)
    finish(job, owner, fields)         update only while WE hold the lease -> False if we lost it
    beat(worker, customer, summary)    the heartbeat (last_tick = now)
    jobs(customer) / heartbeats(customer)   reads
"""

import datetime

JOBS, BEATS = "brain_jobs", "brain_worker_heartbeats"


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class SupabaseJobs:
    def __init__(self, db=None):
        if db is None:
            from supabase_store import db
        self.db = db

    def enqueue(self, row: dict) -> bool:
        saved = (self.db.table(JOBS).upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
                 .execute().data)
        return bool(saved)

    def claim(self, customer: str, owner: str, lease_seconds: int, limit: int) -> list[dict]:
        return self.db.rpc("brain_claim_jobs", {"p_customer": customer, "p_owner": owner,
                                                "p_lease_seconds": lease_seconds, "p_limit": limit}).execute().data or []

    def finish(self, job: dict, owner: str, fields: dict) -> bool:
        return bool(self.db.table(JOBS).update(fields).eq("id", job["id"]).eq("status", "leased")
                    .eq("lease_owner", owner).execute().data)

    def beat(self, worker: str, customer: str, summary: str) -> None:
        self.db.table(BEATS).upsert({"worker": worker, "customer": customer, "last_tick": utc_now().isoformat(),
                                     "last_summary": summary[:500]}, on_conflict="worker,customer").execute()

    def jobs(self, customer: str) -> list[dict]:
        return self.db.table(JOBS).select("*").eq("customer", customer).order("due_at").execute().data

    def heartbeats(self, customer: str) -> list[dict]:
        return self.db.table(BEATS).select("*").eq("customer", customer).execute().data
