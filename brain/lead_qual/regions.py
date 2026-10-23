"""
lead_qual/regions.py  -  Indian states and union territories -> region (code, no LLM).

A delivery criterion may name states, regions or 'pan-India'; a lead names a state. region_of() and
place_matches() let check.py compare the two without the LLM.
"""

REGIONS = {
    "north": ["jammu and kashmir", "ladakh", "himachal pradesh", "punjab", "chandigarh", "haryana", "delhi",
              "uttarakhand", "uttar pradesh", "rajasthan"],
    "central": ["madhya pradesh", "chhattisgarh"],
    "east": ["bihar", "jharkhand", "odisha", "west bengal"],
    "northeast": ["assam", "arunachal pradesh", "manipur", "meghalaya", "mizoram", "nagaland", "tripura", "sikkim"],
    "west": ["gujarat", "maharashtra", "goa", "dadra and nagar haveli and daman and diu"],
    "south": ["andhra pradesh", "telangana", "karnataka", "kerala", "tamil nadu", "puducherry", "lakshadweep",
              "andaman and nicobar islands"],
}
STATE_REGION = {state: region for region, states in REGIONS.items() for state in states}
ALIASES = {"delhi ncr": "delhi", "ncr": "delhi", "new delhi": "delhi", "orissa": "odisha", "pondicherry": "puducherry",
           "north india": "north", "south india": "south", "east india": "east", "west india": "west",
           "north east": "northeast", "north-east": "northeast", "central india": "central"}
ALL_INDIA = {"pan-india", "pan india", "all india", "all-india", "across india", "nationwide", "all over india",
             "india", "all states"}


def clean(place: str | None) -> str:
    """Lower case, single spaces, aliases resolved ('Delhi NCR' -> 'delhi', 'South India' -> 'south')."""
    text = " ".join((place or "").lower().replace("&", "and").split()).strip(" .")
    return ALIASES.get(text, text)


def region_of(state: str | None) -> str | None:
    """'Tamil Nadu' -> 'south'; an unknown name -> None."""
    return STATE_REGION.get(clean(state))


def is_state(place: str | None) -> bool:
    return clean(place) in STATE_REGION


def is_india(country: str | None) -> bool:
    """'India', 'Bharat', 'IN' -> True; 'UAE', 'Dubai' -> False."""
    return clean(country) in {"india", "bharat", "in", "republic of india"}


def place_matches(state: str, places: list[str]) -> bool:
    """True if the state is in the list: by name, by its region, or because the list says pan-India."""
    names = {clean(p) for p in places}
    return bool(names & ALL_INDIA) or clean(state) in names or region_of(state) in names
