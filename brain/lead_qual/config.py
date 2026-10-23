"""
lead_qual/config.py  -  the scoring thresholds, in ONE place (global, not per customer).

Score % = earned / total x 100 (check.py): pass = full weight, unknown = half, fail = 0.
    score >= VERIFIED_AT          -> verified
    NEEDS_HUMAN_AT <= score < VERIFIED_AT -> needs_human
    score < NEEDS_HUMAN_AT        -> not_verified
A failed HARD criterion -> not_verified whatever the score; an unknown HARD criterion -> at most needs_human.
"""

VERIFIED_AT = 80.0
NEEDS_HUMAN_AT = 60.0
DEFAULT_WEIGHT = 1.0          # given by code to a criterion the LLM did not weigh (the CAM must confirm it)
