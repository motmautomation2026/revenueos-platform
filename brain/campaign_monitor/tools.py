"""
campaign_monitor/tools.py  -  the FREE reads of one check, behind one small adapter (the selftest passes a fake).

    calls_status(c)    Quickhowl GET /campaigns/<ref>                     (quickhowl.campaign_status)
    calls(c)           Quickhowl GET /campaigns/<ref>/calls, every page    (quickhowl.cmd_results, quiet)
    email_status(c)    the email campaign's status now         -> None: no status endpoint in our code yet
    email_results(c)   one row per recipient per step sent: {recipient, step, sent_at, delivered, bounced, opened,
                       replied, unsubscribed}               -> None: the results endpoint is not available yet (part 7)
None = not available: the check says so and never calls that a stall.
"""


class RealTools:
    def calls_status(self, campaign: dict) -> dict:
        from quickhowl import campaign_status
        return campaign_status(campaign)

    def calls(self, campaign: dict) -> list[dict]:
        # shortcut: reads every call at each check; pass Quickhowl's 'since' cursor once campaigns get large
        from quickhowl import cmd_results
        return cmd_results(campaign, quiet=True)["calls"]

    def email_status(self, campaign: dict) -> str | None:
        return None

    def email_results(self, campaign: dict) -> list[dict] | None:
        return None
