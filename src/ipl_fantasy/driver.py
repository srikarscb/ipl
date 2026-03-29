"""Playwright-based UI automation driver for IPL Fantasy team management.

Drives the fantasy.iplt20.com website by clicking elements, filling
forms, and scraping DOM content. All interactions happen through
the Playwright Page API and JavaScript evaluation.
"""

import logging
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from ipl_fantasy.models import Player
from ipl_fantasy.notify import Telegram

log = logging.getLogger(__name__)

HOME_URL = "https://fantasy.iplt20.com/classic/home"
SCREENSHOT_DIR = Path("/tmp")

# The four role tabs on the team management page
ROLE_TABS = ["WK", "BAT", "AR", "BOWL"]

# CSS class that marks a player as currently in the user's team
SELECTED_PLAYER_SELECTOR = "li.m11c-remove"


class FantasyDriver:
    """Automates team management on the IPL Fantasy website.

    Each method performs exactly one UI operation (navigate, scrape,
    click) and communicates status via Telegram.
    """

    def __init__(self, browser: BrowserContext, telegram: Telegram) -> None:
        self.browser = browser
        self.telegram = telegram
        self.page: Page = browser.new_page()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def go_home(self) -> dict:
        """Navigate to the fantasy home page and check for manage button.

        Returns a dict with 'has_manage_team' indicating whether the
        transfer window is currently open.
        """
        self.telegram.send_message("Navigating to home page...")
        self.page.goto(HOME_URL, wait_until="networkidle")
        self.page.wait_for_timeout(2000)

        self._screenshot("home")

        # Check if the "Manage Team" button is present on the page
        body_text = self.page.inner_text("body")
        has_manage_team = "Manage Team" in body_text

        log.info("Home page: has_manage_team=%s", has_manage_team)
        return {"has_manage_team": has_manage_team}

    def go_manage_team(self) -> None:
        """Click 'Manage Team' to open the team management interface."""
        self.telegram.send_message("Opening team management...")
        self.page.click("text=Manage Team")
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(3000)

        self._screenshot("manage_team")

    # ------------------------------------------------------------------
    # Team scraping
    # ------------------------------------------------------------------

    def get_current_team(self) -> list[Player]:
        """Scrape the current team by clicking through each role tab.

        Iterates WK -> BAT -> AR -> BOWL tabs, collecting selected
        players from each. De-duplicates by name in case a player
        appears under multiple tabs.

        Returns a list of Player models.
        """
        all_players: list[Player] = []
        seen_names: set[str] = set()

        for role in ROLE_TABS:
            self._click_role_tab(role)
            tab_players = self._scrape_selected_players(role)

            # De-duplicate — a player should only appear once
            for p in tab_players:
                if p.name and p.name not in seen_names:
                    seen_names.add(p.name)
                    all_players.append(p)

            log.info("Tab %s: found %d selected players", role, len(tab_players))

        log.info("Total team: %d players", len(all_players))
        for p in all_players:
            log.info("  %s", p)

        return all_players

    def _click_role_tab(self, role: str) -> None:
        """Click a role tab (WK/BAT/AR/BOWL) on the team management page.

        Uses JS evaluation to find the tab element by matching text
        content, since the tabs don't have reliable IDs or classes.
        The JS is passed as a raw string (r-string) to prevent Python
        from interpreting backslash sequences like \\n.
        """
        js = r"""(role) => {
            const els = document.querySelectorAll('*');
            for (const el of els) {
                const t = el.textContent.trim();
                if (t.startsWith(role + ' ') || t.startsWith(role + '\n') || t === role) {
                    if (el.children.length <= 2 && el.tagName !== 'BODY') {
                        el.click();
                        return;
                    }
                }
            }
        }"""
        self.page.evaluate(js, role)
        self.page.wait_for_timeout(1000)

    def _scrape_selected_players(self, role: str) -> list[Player]:
        """Scrape all selected players from the currently visible tab.

        Selected players have the 'm11c-remove' class on their <li>.
        Player name, team, captain/VC status are extracted from child elements.
        """
        raw_players = self.page.evaluate(
            """() => {
                const items = document.querySelectorAll('li.m11c-remove');
                return Array.from(items).map(li => {
                    // Player name is in the first span inside .m11c-plyrSel__name
                    const nameSpan = li.querySelector('.m11c-plyrSel__name > span:first-child');
                    const teamEl = li.querySelector('.m11c-plyrSel__team span');

                    const name = nameSpan ? nameSpan.textContent.trim() : '';
                    const team = teamEl ? teamEl.textContent.trim() : '';

                    // Captain/VC badges use CSS classes containing 'cBadge--c' / 'cBadge--vc'
                    const isCaptain = li.querySelector('[class*="cBadge--c"]') !== null;
                    const isVC = li.querySelector('[class*="cBadge--vc"]') !== null;

                    return { name, team, isCaptain, isVC };
                });
            }"""
        )

        # Convert raw dicts from JS into Player models
        return [
            Player(
                name=p["name"],
                team=p["team"],
                role=role,
                is_captain=p["isCaptain"],
                is_vice_captain=p["isVC"],
            )
            for p in raw_players
        ]

    # ------------------------------------------------------------------
    # Team display
    # ------------------------------------------------------------------

    def format_team(self, players: list[Player]) -> str:
        """Format the team roster as a numbered list for Telegram display."""
        lines = ["Your Current Team:\n"]
        for i, p in enumerate(players, 1):
            tag = " [C]" if p.is_captain else " [VC]" if p.is_vice_captain else ""
            lines.append(f"{i}. {p.name} ({p.team}, {p.role}){tag}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Player swaps (transfers)
    # ------------------------------------------------------------------

    def swap_player(self, out_name: str, in_name: str) -> bool:
        """Replace one player with another in the team.

        Two-step process:
          1. Remove the outgoing player by clicking them
          2. Search for and add the incoming player
        """
        self.telegram.send_message(f"Swapping {out_name} -> {in_name}...")

        if not self._remove_player(out_name):
            self.telegram.send_message(f"Could not find '{out_name}' in your team.")
            return False

        if not self._add_player(in_name):
            self.telegram.send_message(
                f"Could not find '{in_name}' in available players."
            )
            return False

        self._screenshot("after_swap")
        self.telegram.send_message(f"Swapped {out_name} -> {in_name}")
        return True

    def _remove_player(self, name: str) -> bool:
        """Click on a player in the team to deselect/remove them.

        Finds the player by matching name text within selected player elements.
        """
        clicked = self.page.evaluate(
            """(outName) => {
                const items = document.querySelectorAll('li.m11c-remove');
                for (const li of items) {
                    if (li.textContent.toLowerCase().includes(outName.toLowerCase())) {
                        li.click();
                        return true;
                    }
                }
                return false;
            }""",
            name,
        )
        if clicked:
            self.page.wait_for_timeout(1000)
        return clicked

    def _add_player(self, name: str) -> bool:
        """Search for and select a player to add to the team.

        Uses the search box (if available) to filter the player list,
        then clicks on the matching player card.
        """
        # Try to narrow the player list using the search box
        search_box = self.page.locator(
            'input[placeholder*="search" i], input[placeholder*="Search" i], input[type="search"]'
        )
        if search_box.count() > 0:
            search_box.first.fill(name)
            self.page.wait_for_timeout(1000)

        # Click on the first available (not-selected) player matching the name
        clicked = self.page.evaluate(
            """(inName) => {
                // Available players are <li> elements WITHOUT the m11c-remove class
                const items = document.querySelectorAll('li:not(.m11c-remove)');
                for (const li of items) {
                    if (li.textContent.toLowerCase().includes(inName.toLowerCase())) {
                        li.click();
                        return true;
                    }
                }
                return false;
            }""",
            name,
        )
        if clicked:
            self.page.wait_for_timeout(2000)
        return clicked

    # ------------------------------------------------------------------
    # Captain / Vice-Captain selection
    # ------------------------------------------------------------------

    def set_captain(self, name: str) -> bool:
        """Set a player as team captain."""
        return self._set_player_role(name, role_type="captain")

    def set_vice_captain(self, name: str) -> bool:
        """Set a player as team vice-captain."""
        return self._set_player_role(name, role_type="vice-captain")

    def _set_player_role(self, name: str, role_type: str) -> bool:
        """Set a player as captain or vice-captain by clicking their badge.

        The badge CSS class contains 'captain' or 'cBadge' for captain,
        and 'vice' or 'vcBadge' for vice-captain.

        If no badge button is found, falls back to clicking the player
        element itself (which may open a role-selection popup).
        """
        self.telegram.send_message(f"Setting {role_type}: {name}...")

        # Build the CSS selector based on role type
        if role_type == "captain":
            badge_selector = '[class*="captain"], [class*="cBadge"]'
        else:
            badge_selector = '[class*="vice"], [class*="vcBadge"]'

        result = self.page.evaluate(
            """(args) => {
                const [playerName, badgeSelector] = args;
                const items = document.querySelectorAll('li.m11c-remove');
                for (const li of items) {
                    if (li.textContent.toLowerCase().includes(playerName.toLowerCase())) {
                        // Try clicking the badge button directly
                        const badge = li.querySelector(badgeSelector);
                        if (badge) {
                            badge.click();
                            return 'clicked_badge';
                        }
                        // Fall back to clicking the player to open options
                        li.click();
                        return 'clicked_player';
                    }
                }
                return 'not_found';
            }""",
            [name, badge_selector],
        )

        self.page.wait_for_timeout(2000)
        self._screenshot(f"after_{role_type.replace('-', '_')}")

        if result == "not_found":
            self.telegram.send_message(
                f"Could not find '{name}' in team for {role_type}."
            )
            return False

        msg = f"{role_type.title()} action taken for {name} (result: {result})"
        self.telegram.send_message(msg)
        return True

    # ------------------------------------------------------------------
    # Save / confirm
    # ------------------------------------------------------------------

    def save_team(self) -> None:
        """Click the save/confirm button to finalize team changes.

        Tries several common button labels since the site uses
        different text depending on the action context.
        """
        for text in ["Save", "Confirm", "Next", "Submit"]:
            btn = self.page.locator(f"text={text}").first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                self.page.wait_for_timeout(3000)
                self._screenshot("after_save")
                self.telegram.send_message(f"Clicked '{text}' to save changes.")
                return

        self.telegram.send_message("Could not find a save button.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _screenshot(self, label: str) -> None:
        """Take a screenshot and send it to Telegram for debugging."""
        path = str(SCREENSHOT_DIR / f"ipl_{label}.png")
        self.page.screenshot(path=path)
        self.telegram.send_photo(path, caption=label)

    def inspect_player_dots(self) -> None:
        """Send the raw HTML of player elements to Telegram for inspection.

        Captures the outerHTML of the first selected and first available
        player <li> elements so we can identify the CSS selectors for
        the green/red/blue dot indicators.
        """
        html = self.page.evaluate(
            r"""() => {
                const selected = document.querySelector('li.m11c-remove');
                const available = document.querySelector('li:not(.m11c-remove)');
                return {
                    selected: selected ? selected.outerHTML : 'none found',
                    available: available ? available.outerHTML : 'none found',
                };
            }"""
        )

        self.telegram.send_message(
            "DOT INSPECTION - Selected player HTML:\n\n"
            + html["selected"][:3000]
        )
        self.telegram.send_message(
            "DOT INSPECTION - Available player HTML:\n\n"
            + html["available"][:3000]
        )

    def close(self) -> None:
        """Close the browser page."""
        self.page.close()
