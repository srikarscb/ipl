"""Google-based playing XI checker.

Determines whether a player is in the confirmed playing XI by
searching Google for recent Cricbuzz/ESPNcricinfo articles about
today's match and analyzing the search result snippets.
"""

import logging

from playwright.sync_api import BrowserContext

from ipl_fantasy.models import Player

log = logging.getLogger(__name__)

# Words in Google snippets that indicate a player is NOT playing
NEGATIVE_SIGNALS = [
    "not in playing xi",
    "not part of playing xi",
    "dropped",
    "benched",
    "ruled out",
    "excluded",
    "misses out",
    "replaced by",
    "sits out",
    "left out",
    "not included",
    "on the bench",
    "unavailable",
]

# Words in Google snippets that indicate a player IS playing
POSITIVE_SIGNALS = [
    "playing xi",
    "confirmed xi",
    "in the xi",
    "retained",
    "included",
    "makes the xi",
]


def check_players(
    browser: BrowserContext,
    players: list[Player],
    today_teams: set[str],
) -> dict[str, bool]:
    """Check which players from today's teams are in the playing XI.

    Only checks players whose team is in today_teams — players
    from other teams are skipped (they're not playing today
    regardless, but we keep them in the fantasy team).

    Returns a dict mapping player name -> True (playing) / False (not playing).
    Players not from today's teams are excluded from the result.
    """
    result: dict[str, bool] = {}

    # Build the match string for the search query (e.g. "CSK vs RCB")
    teams_list = sorted(today_teams)
    match_str = " vs ".join(teams_list)

    for player in players:
        # Only check players from teams playing today
        if player.team not in today_teams:
            continue

        is_playing = _is_player_playing(browser, player.name, match_str)
        result[player.name] = is_playing

        status = "PLAYING" if is_playing else "NOT PLAYING"
        log.info("%s (%s): %s", player.name, player.team, status)

    return result


def _is_player_playing(
    browser: BrowserContext,
    player_name: str,
    match_str: str,
) -> bool:
    """Search Google to determine if a player is in today's playing XI.

    Opens a Playwright page, searches Google, reads the result
    snippets, and looks for positive/negative signals.

    Returns True if the player appears to be playing (or if
    results are ambiguous — we default to "playing" to avoid
    unnecessary swaps).
    """
    page = browser.new_page()
    query = (
        f"{player_name} playing XI today {match_str} "
        f"site:cricbuzz.com OR site:espncricinfo.com"
    )

    try:
        # Navigate to Google search
        page.goto(
            f"https://www.google.com/search?q={query}",
            wait_until="networkidle",
        )
        page.wait_for_timeout(2000)

        # Extract all visible text from search results
        snippet_text = _extract_search_snippets(page)
        if not snippet_text:
            log.warning(
                "No search results for %s — assuming playing", player_name
            )
            return True

        return _analyze_snippets(snippet_text, player_name)
    except Exception:
        log.exception("Google search failed for %s", player_name)
        # Default to playing if search fails — don't make bad swaps
        return True
    finally:
        page.close()


def _extract_search_snippets(page) -> str:
    """Extract text from Google search result snippets.

    Grabs the main search results area and returns all visible
    text, which includes titles, snippets, and highlighted text.
    """
    # Google's main results container
    selectors = ["#search", "#rso", "div[data-async-context]"]

    for selector in selectors:
        el = page.locator(selector).first
        if el.count() > 0:
            return el.inner_text()

    # Fallback: grab all body text
    return page.inner_text("body")


def _analyze_snippets(snippet_text: str, player_name: str) -> bool:
    """Analyze Google snippet text for playing XI status signals.

    Checks for negative signals first (dropped, benched, etc.),
    then positive signals (in playing XI, confirmed, etc.).

    Returns True if playing, False if not playing.
    Defaults to True if ambiguous (avoid unnecessary swaps).
    """
    text_lower = snippet_text.lower()
    player_lower = player_name.lower()

    # Only analyze snippets that mention the player
    if player_lower not in text_lower:
        log.warning(
            "Player %s not mentioned in results — assuming playing",
            player_name,
        )
        return True

    # Check for negative signals first — these are stronger indicators
    for signal in NEGATIVE_SIGNALS:
        if signal in text_lower:
            log.info(
                "Negative signal for %s: '%s' found in snippets",
                player_name,
                signal,
            )
            return False

    # Check for positive signals
    for signal in POSITIVE_SIGNALS:
        if signal in text_lower:
            log.info(
                "Positive signal for %s: '%s' found in snippets",
                player_name,
                signal,
            )
            return True

    # Ambiguous — default to playing to avoid bad swaps
    log.info(
        "Ambiguous results for %s — assuming playing",
        player_name,
    )
    return True
