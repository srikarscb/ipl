"""Set captain and vice-captain. Names come from CAPTAIN/VICE_CAPTAIN env vars."""

import logging
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from ipl_fantasy.auth import create_browser, login
from ipl_fantasy.config import Settings
from ipl_fantasy.driver import FantasyDriver
from ipl_fantasy.notify import Telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

CAPTAIN = os.environ.get("CAPTAIN", "Marsh")
VICE_CAPTAIN = os.environ.get("VICE_CAPTAIN", "Pant")


def _click_footer_btn(page, label: str) -> None:
    """Click a footer button (div.m11c-btn) by its text label."""
    page.evaluate("""(label) => {
        const btns = document.querySelectorAll('.m11c-btn');
        for (const btn of btns) {
            if (btn.textContent.trim() === label) {
                btn.click();
                return;
            }
        }
    }""", label)


def _click_captain_badge(page, player_name: str, badge: str) -> str:
    """Click the C or VC badge for a player on the captain selection screen.

    badge should be 'C' or 'VC'.
    The badge element is: <div class="m11c-plyrSel__cap"><span>C</span></div>
    inside a <li> that contains the player name.
    """
    return page.evaluate("""([playerName, badge]) => {
        const lis = document.querySelectorAll('li');
        for (const li of lis) {
            if (!li.textContent.includes(playerName)) continue;
            // Find the m11c-plyrSel__cap divs
            const caps = li.querySelectorAll('.m11c-plyrSel__cap');
            for (const cap of caps) {
                const span = cap.querySelector('span');
                if (span && span.textContent.trim() === badge) {
                    cap.click();
                    return 'clicked ' + badge + ' for ' + playerName;
                }
            }
            return 'found ' + playerName + ' but no ' + badge + ' badge';
        }
        return playerName + ' not found';
    }""", [player_name, badge])


def main() -> None:
    settings = Settings()
    telegram = Telegram(settings)
    telegram.send_message(f"Setting C: {CAPTAIN}, VC: {VICE_CAPTAIN}...")

    pw, browser = create_browser()
    try:
        login(browser, settings, telegram)
        driver = FantasyDriver(browser, telegram)

        # Mobile viewport to avoid branding panel
        driver.page.set_viewport_size({"width": 430, "height": 932})

        try:
            page = driver.page

            # Navigate to team page
            home_info = driver.go_home()
            if not home_info["has_manage_team"]:
                telegram.send_message("Transfer window may be closed.")
                return

            driver.go_manage_team()

            # Step 1: Click "Next" to go to captain selection screen
            _click_footer_btn(page, "Next")
            page.wait_for_timeout(5000)
            driver._screenshot("captain_screen")

            # Step 2: Click C for Jadeja
            c_result = _click_captain_badge(page, CAPTAIN, "C")
            telegram.send_message(f"Captain: {c_result}")
            page.wait_for_timeout(2000)
            driver._screenshot("after_captain")

            # Step 3: Click VC for Gill
            vc_result = _click_captain_badge(page, VICE_CAPTAIN, "VC")
            telegram.send_message(f"VC: {vc_result}")
            page.wait_for_timeout(2000)
            driver._screenshot("after_vc")

            # Step 4: Click "Next" to go to confirmation screen
            _click_footer_btn(page, "Next")
            page.wait_for_timeout(5000)
            driver._screenshot("confirmation_screen")

            # Step 5: Click "Save" or "Confirm" on confirmation screen
            # Try all possible button labels
            for label in ["Save", "Confirm", "Save Team", "Submit"]:
                _click_footer_btn(page, label)
            page.wait_for_timeout(5000)
            driver._screenshot("final")
            telegram.send_message("Done!")

        finally:
            driver.close()
    except Exception:
        logging.exception("Failed")
        telegram.send_message("Captaincy script failed. Check logs.")
    finally:
        browser.close()
        pw.stop()


if __name__ == "__main__":
    main()
