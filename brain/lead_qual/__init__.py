"""
lead_qual/  -  Lead Qualification Agent (brain logic only; nothing else imports it yet).

Does a lead fit what the customer can actually supply? The LLM only READS (the customer's document, each
lead); CODE compares and decides. See README.md. Run:  python -m lead_qual criteria | approve | check <file> |
answer <lead_id> verified|not_verified "note" | selftest
"""
