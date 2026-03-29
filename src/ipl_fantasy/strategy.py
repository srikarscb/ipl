"""Swap decision logic for auto-managing the fantasy team.

Determines which players need to be swapped out (not in playing XI)
and finds valid replacements from available players in the same role.
"""

import logging
from collections import Counter

from playwright.sync_api import BrowserContext

from ipl_fantasy.driver import FantasyDriver
from ipl_fantasy.models import Player
from ipl_fantasy.playing_xi import _is_player_playing

log = logging.getLogger(__name__)

# IPL Fantasy max players from a single franchise
MAX_PER_FRANCHISE = 7


def find_players_not_playing(
    players: list[Player],
    playing_status: dict[str, bool],
) -> list[Player]:
    """Return players from the fantasy team who are NOT in the playing XI.

    Only considers players that were checked (i.e., from today's teams).
    Players from other teams are not included.
    """
    return [
        p
        for p in players
        if p.name in playing_status and not playing_status[p.name]
    ]


def find_replacement(
    browser: BrowserContext,
    driver: FantasyDriver,
    out_player: Player,
    match_str: str,
    current_team: list[Player],
) -> str | None:
    """Find a valid replacement for a player not in the playing XI.

    Looks at available players in the same role tab on the fantasy
    site, checks each against Google to confirm they're playing,
    and verifies the franchise cap won't be exceeded.

    Returns the replacement player's name, or None if no valid swap.
    """
    # Count current franchise distribution (excluding the outgoing player)
    franchise_count = Counter(
        p.team for p in current_team if p.name != out_player.name
    )

    # Click the role tab to see available players for this role
    driver._click_role_tab(out_player.role)

    # Scrape available (not selected) players from the UI
    candidates = _scrape_available_players(driver)
    log.info(
        "Found %d available candidates for %s replacement",
        len(candidates),
        out_player.role,
    )

    for name, team in candidates:
        # Skip if adding this player would exceed franchise cap
        if franchise_count.get(team, 0) >= MAX_PER_FRANCHISE:
            log.info(
                "Skipping %s (%s) — franchise cap reached", name, team
            )
            continue

        # Check if this candidate is in the playing XI
        if _is_player_playing(browser, name, match_str):
            log.info("Found replacement: %s (%s)", name, team)
            return name

    log.warning(
        "No valid replacement found for %s (%s)",
        out_player.name,
        out_player.role,
    )
    return None


def _scrape_available_players(driver: FantasyDriver) -> list[tuple[str, str]]:
    """Scrape available (not selected) players from the current role tab.

    Returns a list of (name, team) tuples for players that can be
    added to the team (they have no 'm11c-remove' class).
    """
    raw = driver.page.evaluate(
        r"""() => {
            // Available players are <li> elements WITHOUT m11c-remove
            const items = document.querySelectorAll('li:not(.m11c-remove)');
            const players = [];
            for (const li of items) {
                const nameSpan = li.querySelector(
                    '.m11c-plyrSel__name > span:first-child'
                );
                const teamEl = li.querySelector('.m11c-plyrSel__team span');

                if (nameSpan) {
                    players.push({
                        name: nameSpan.textContent.trim(),
                        team: teamEl ? teamEl.textContent.trim() : '',
                    });
                }
            }
            return players;
        }"""
    )

    return [(p["name"], p["team"]) for p in raw if p["name"]]
