"""Main bot orchestration — ties together auth, UI driver, and Telegram.

Two entry points:
  - run()      — interactive mode, waits for user instructions via Telegram
  - run_auto() — cron mode, auto-swaps players not in today's playing XI
"""

import logging

from ipl_fantasy.auth import create_browser, login
from ipl_fantasy.config import Settings
from ipl_fantasy.driver import FantasyDriver
from ipl_fantasy.models import Player
from ipl_fantasy.notify import Telegram
from ipl_fantasy.playing_xi import check_players
from ipl_fantasy.schedule import get_today_matches, get_today_teams
from ipl_fantasy.strategy import find_players_not_playing, find_replacement

log = logging.getLogger(__name__)

# How long to wait for user replies via Telegram (seconds)
REPLY_TIMEOUT = 60


def run() -> None:
    """Main entry point — runs the full bot workflow end-to-end.

    Orchestrates setup, authentication, team reading, user interaction,
    and cleanup. Each step is delegated to a focused helper function.
    """
    _setup_logging()

    settings = Settings()
    telegram = Telegram(settings)
    telegram.send_message("IPL Fantasy Bot starting up...")

    log.info("Starting bot...")
    pw, browser = create_browser()

    try:
        login(browser, settings, telegram)
        driver = FantasyDriver(browser, telegram)

        try:
            # Read the current team and check if transfers are open
            players = _read_team(driver, telegram)
            if players is None:
                return

            # Collect and apply user's transfer + captain instructions
            changes = _process_user_instructions(driver, telegram, players)

            # Save if anything changed, then send summary
            _finalize(driver, telegram, changes)
        finally:
            driver.close()
    except Exception:
        # Send the error to Telegram so the user knows what went wrong,
        # instead of silently crashing with a traceback on the server
        log.exception("Bot failed")
        telegram.send_message("Bot encountered an error and stopped. Check logs.")
    finally:
        browser.close()
        pw.stop()


# ======================================================================
# Workflow steps
# ======================================================================


def _setup_logging() -> None:
    """Configure the root logger format and level."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


def _read_team(driver: FantasyDriver, telegram: Telegram) -> list[Player] | None:
    """Navigate to team page and scrape the current roster.

    Returns None if the transfer window is closed or the team
    can't be read (caller should abort).
    """
    home_info = driver.go_home()

    if not home_info["has_manage_team"]:
        telegram.send_message(
            "No 'Manage Team' button found. Transfer window may be closed."
        )
        return None

    driver.go_manage_team()

    players = driver.get_current_team()
    if not players:
        telegram.send_message("Could not read team from page. Check the screenshots.")
        return None

    return players


def _process_user_instructions(
    driver: FantasyDriver, telegram: Telegram, players: list[Player]
) -> list[str]:
    """Prompt the user for transfers and captain picks, apply them.

    Returns a list of human-readable change descriptions.
    """
    changes: list[str] = []

    # Show current team and ask for transfers
    team_msg = driver.format_team(players)
    team_msg += "\n\nTransfers: Reply 'swap PlayerOut -> PlayerIn' or 'skip'"
    telegram.send_message(team_msg)

    changes.extend(_handle_transfers(driver, telegram))
    changes.extend(_handle_captaincy(driver, telegram))

    return changes


def _handle_transfers(driver: FantasyDriver, telegram: Telegram) -> list[str]:
    """Wait for transfer instructions and execute swaps.

    Parses lines like "swap PlayerOut -> PlayerIn" from the
    user's reply. Multiple swaps can be sent on separate lines.

    Returns list of change descriptions for the summary.
    """
    changes: list[str] = []

    reply = telegram.wait_for_reply(timeout=REPLY_TIMEOUT)
    if not reply or reply.lower() == "skip":
        return changes

    for line in reply.split("\n"):
        swap = _parse_swap(line)
        if not swap:
            continue

        out_name, in_name = swap
        if driver.swap_player(out_name, in_name):
            changes.append(f"OUT: {out_name} -> IN: {in_name}")

    return changes


def _parse_swap(line: str) -> tuple[str, str] | None:
    """Parse a single swap instruction like 'swap Kohli -> Dhoni'.

    Returns (out_name, in_name) or None if the line isn't a swap.
    """
    line = line.strip()

    # Must start with "swap" (case-insensitive)
    if not line.lower().startswith("swap"):
        return None

    # Strip the "swap" prefix, split on the arrow
    rest = line[4:].strip()
    if "->" not in rest:
        return None

    out_name, _, in_name = rest.partition("->")
    out_name, in_name = out_name.strip(), in_name.strip()

    if not out_name or not in_name:
        return None

    return out_name, in_name


def _handle_captaincy(driver: FantasyDriver, telegram: Telegram) -> list[str]:
    """Ask user for captain/vice-captain picks and apply them.

    Expects format: "C: PlayerName, VC: PlayerName"

    Returns list of change descriptions for the summary.
    """
    changes: list[str] = []

    telegram.send_message(
        "Captain & Vice-Captain:\nReply 'C: PlayerName, VC: PlayerName' or 'skip'"
    )

    reply = telegram.wait_for_reply(timeout=REPLY_TIMEOUT)
    if not reply or reply.lower() == "skip":
        return changes

    captain, vice_captain = _parse_captaincy(reply)

    if captain and driver.set_captain(captain):
        changes.append(f"Captain: {captain}")

    if vice_captain and driver.set_vice_captain(vice_captain):
        changes.append(f"Vice-Captain: {vice_captain}")

    return changes


def _parse_captaincy(reply: str) -> tuple[str | None, str | None]:
    """Parse captain and vice-captain names from a reply string.

    Input format: "C: PlayerName, VC: PlayerName"
    Either part can be omitted. Returns (captain, vice_captain).
    """
    captain = None
    vice_captain = None

    # Split on comma to handle "C: Kohli, VC: Dhoni"
    for part in reply.split(","):
        part = part.strip()

        # Check for "VC:" first since "C:" would also match the "C" in "VC"
        if part.upper().startswith("VC:"):
            vice_captain = part[3:].strip()
        elif part.upper().startswith("C:"):
            captain = part[2:].strip()

    return captain or None, vice_captain or None


# ======================================================================
# Auto mode (cron) — no user input
# ======================================================================


def run_auto() -> None:
    """Cron entry point — auto-swap players not in today's playing XI.

    Flow:
      1. Check if there's an IPL match today
      2. Login and read the fantasy team
      3. Filter to players from today's teams
      4. Google-check each player's playing XI status
      5. Swap out anyone not playing for a confirmed playing replacement
      6. Save and notify via Telegram
    """
    _setup_logging()

    settings = Settings()
    telegram = Telegram(settings)

    # Check schedule before doing anything else
    matches = get_today_matches()
    if not matches:
        telegram.send_message("No IPL match today. Nothing to do.")
        return

    today_teams = get_today_teams()
    match_str = ", ".join(f"{t1} vs {t2}" for t1, t2 in matches)
    telegram.send_message(f"Auto-swap bot starting. Today: {match_str}")

    log.info("Starting auto-swap bot...")
    pw, browser = create_browser()

    try:
        login(browser, settings, telegram)
        driver = FantasyDriver(browser, telegram)

        try:
            # Read current team
            players = _read_team(driver, telegram)
            if players is None:
                return

            # Inspect player element HTML for dot selectors
            # so we can see if green/red/blue dots are in the DOM
            driver.inspect_player_dots()

            # Filter to players from today's teams
            relevant = [p for p in players if p.team in today_teams]
            if not relevant:
                telegram.send_message(
                    "No players from today's teams in your fantasy XI."
                )
                return

            names = ", ".join(f"{p.name} ({p.team})" for p in relevant)
            telegram.send_message(
                f"Checking playing XI for: {names}"
            )

            # Google-check each relevant player
            playing_status = check_players(
                browser, players, today_teams
            )

            # Find who's not playing
            not_playing = find_players_not_playing(players, playing_status)
            if not not_playing:
                telegram.send_message(
                    "All your players are in the playing XI!"
                )
                return

            out_names = ", ".join(f"{p.name} ({p.team})" for p in not_playing)
            telegram.send_message(f"Not in playing XI: {out_names}")

            # Build match string for Google searches during replacement
            match_search = " vs ".join(sorted(today_teams))

            # Find replacements and execute swaps
            changes: list[str] = []
            for player in not_playing:
                replacement = find_replacement(
                    browser, driver, player, match_search, players
                )
                if replacement:
                    if driver.swap_player(player.name, replacement):
                        changes.append(
                            f"OUT: {player.name} -> IN: {replacement}"
                        )
                else:
                    telegram.send_message(
                        f"No replacement found for {player.name} ({player.role})"
                    )

            # Save and summarize
            if changes:
                driver.save_team()
                summary = (
                    "Auto-swap complete:\n\n"
                    + "\n".join(f"- {c}" for c in changes)
                )
            else:
                summary = "Auto-swap: No valid swaps could be made."

            telegram.send_message(summary)
            driver._screenshot("auto_final")
            log.info("Auto-swap done. Changes: %s", changes)

        finally:
            driver.close()
    except Exception:
        log.exception("Auto-swap bot failed")
        telegram.send_message(
            "Auto-swap bot encountered an error. Check logs."
        )
    finally:
        browser.close()
        pw.stop()


def _finalize(driver: FantasyDriver, telegram: Telegram, changes: list[str]) -> None:
    """Save changes (if any) and send a summary to Telegram."""
    if changes:
        driver.save_team()
        summary = "IPL Fantasy Bot - Summary:\n\n" + "\n".join(
            f"- {c}" for c in changes
        )
    else:
        summary = "IPL Fantasy Bot - No changes made."

    telegram.send_message(summary)
    driver._screenshot("final")
    log.info("Done. Changes: %s", changes)
