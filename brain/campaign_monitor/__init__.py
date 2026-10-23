"""
campaign_monitor/  -  tracks every launched calls / email campaign of a customer until it really ends, and raises
a stall (a day with expected activity but none) as an answered brain_decisions row (kind campaign_stalled).
On its own: nothing imports it. Run: python -m campaign_monitor check [--run <month>] | status | selftest
"""
