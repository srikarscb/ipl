"""IPL 2026 match schedule — maps dates to team matchups.

Used by the auto-swap bot to determine which 2 teams are playing
today, so it only checks playing XI status for relevant players.

Schedule sourced from Yahoo Sports / iplt20.com.
Double-header days have multiple entries per date.
"""

import logging
from datetime import date

log = logging.getLogger(__name__)

# Maps ISO date strings to list of (team1, team2) matchups.
# Double-header days have 2+ tuples in the list.
SCHEDULE: dict[str, list[tuple[str, str]]] = {
    "2026-03-28": [("RCB", "SRH")],
    "2026-03-29": [("MI", "KKR")],
    "2026-03-30": [("CSK", "RR")],
    "2026-03-31": [("PBKS", "GT")],
    "2026-04-01": [("DC", "LSG")],
    "2026-04-02": [("SRH", "KKR")],
    "2026-04-03": [("CSK", "PBKS")],
    "2026-04-04": [("MI", "DC"), ("RR", "GT")],
    "2026-04-05": [("CSK", "RCB"), ("SRH", "LSG")],
    "2026-04-06": [("PBKS", "KKR")],
    "2026-04-07": [("MI", "RR")],
    "2026-04-08": [("DC", "GT")],
    "2026-04-09": [("KKR", "LSG")],
    "2026-04-10": [("RCB", "RR")],
    "2026-04-11": [("CSK", "DC"), ("SRH", "PBKS")],
    "2026-04-12": [("RCB", "MI"), ("LSG", "GT")],
    "2026-04-13": [("SRH", "RR")],
    "2026-04-14": [("CSK", "KKR")],
    "2026-04-15": [("RCB", "LSG")],
    "2026-04-16": [("MI", "PBKS")],
    "2026-04-17": [("KKR", "GT")],
    "2026-04-18": [("RCB", "DC"), ("CSK", "SRH")],
    "2026-04-19": [("KKR", "RR"), ("PBKS", "LSG")],
    "2026-04-20": [("MI", "GT")],
    "2026-04-21": [("SRH", "DC")],
    "2026-04-22": [("RR", "LSG")],
    "2026-04-23": [("CSK", "MI")],
    "2026-04-24": [("RCB", "GT")],
    "2026-04-25": [("SRH", "RR"), ("DC", "PBKS")],
    "2026-04-26": [("CSK", "GT"), ("KKR", "LSG")],
    "2026-04-27": [("RCB", "DC")],
    "2026-04-28": [("PBKS", "RR")],
    "2026-04-29": [("MI", "SRH"), ("PBKS", "RR")],
    "2026-04-30": [("RCB", "GT")],
    "2026-05-01": [("RR", "DC")],
    "2026-05-02": [("CSK", "MI")],
    "2026-05-03": [("KKR", "SRH"), ("GT", "PBKS")],
    "2026-05-04": [("MI", "LSG"), ("RR", "GT")],
    "2026-05-05": [("CSK", "DC")],
    "2026-05-06": [("PBKS", "SRH")],
    "2026-05-07": [("RCB", "LSG")],
    "2026-05-08": [("KKR", "DC")],
    "2026-05-09": [("RR", "GT")],
    "2026-05-10": [("CSK", "LSG"), ("RCB", "MI"), ("RR", "LSG")],
    "2026-05-11": [("DC", "PBKS")],
    "2026-05-12": [("SRH", "GT")],
    "2026-05-13": [("RCB", "KKR")],
    "2026-05-14": [("MI", "PBKS")],
    "2026-05-15": [("CSK", "LSG")],
    "2026-05-16": [("KKR", "GT")],
    "2026-05-17": [("RCB", "PBKS"), ("RR", "DC")],
    "2026-05-18": [("CSK", "SRH")],
    "2026-05-19": [("LSG", "RR")],
    "2026-05-20": [("KKR", "MI")],
    "2026-05-21": [("CSK", "GT")],
    "2026-05-22": [("RCB", "SRH")],
    "2026-05-23": [("PBKS", "LSG")],
    "2026-05-24": [("MI", "RR"), ("KKR", "DC")],
}


def get_today_matches() -> list[tuple[str, str]]:
    """Return the list of matchups for today's date.

    Returns an empty list if there's no IPL match today.
    On double-header days, returns both matchups.
    """
    today = date.today().isoformat()
    matches = SCHEDULE.get(today, [])

    if not matches:
        log.info("No IPL match scheduled for %s", today)
    else:
        for team1, team2 in matches:
            log.info("Today's match: %s vs %s", team1, team2)

    return matches


def get_today_teams() -> set[str]:
    """Return the set of all teams playing today.

    Useful for filtering the fantasy team to only players
    from today's teams.
    """
    teams: set[str] = set()
    for team1, team2 in get_today_matches():
        teams.add(team1)
        teams.add(team2)
    return teams
