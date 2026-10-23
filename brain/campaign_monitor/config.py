"""
campaign_monitor/config.py  -  the settings, in one place.
"""

# False (now): a stall is RECORDED as an answered decision (kind campaign_stalled, answer 'raised', decided_by brain):
#   it shows in the platform's activity feed, the log and 'status', and blocks nothing.
# True (later): a stall is OPENED as a decision for a person. Only switch it on when (1) the platform knows the kind
#   (lib/brain/answers.ts + the People's tasks screen) and (2) state_store lets the kind be opened, with an answer
#   'close' can use (e.g. {"seen", "stop"}). Until then state_store refuses to open it (STOPPED, nothing written).
OPEN_ALERTS = False

ALERT_KIND = "campaign_stalled"

# Quickhowl's calling window when the tool does not say (its server default).
CALL_WINDOW = "09:00-19:00 Mon-Sat"
# The launch day only counts if at least this much of the window was left after the launch.
LAUNCH_DAY_MIN_HOURS = 1
