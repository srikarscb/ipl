"""Authentication and browser lifecycle management.

Handles:
  - Creating a persistent Playwright browser (session cookies survive restarts)
  - Logging into fantasy.iplt20.com via email + OTP
  - Automatically fetching OTP codes from Gmail via IMAP
"""

import email
import imaplib
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright

from ipl_fantasy.config import Settings
from ipl_fantasy.notify import Telegram

log = logging.getLogger(__name__)

# Persistent browser data dir — keeps cookies/session across runs
BROWSER_DATA_DIR = Path.home() / ".ipl-browser"

# IPL Fantasy URLs
LOGIN_URL = "https://fantasy.iplt20.com/my11c/static/login.html?ru=/classic/home"
HOME_URL = "https://fantasy.iplt20.com/classic/home"

# Where to save debug screenshots
SCREENSHOT_DIR = Path("/tmp")

# How long to wait for OTP email before giving up (seconds)
OTP_TIMEOUT = 90

# Minimum digits for a token to be considered an OTP (avoids matching "12", "0", etc.)
OTP_MIN_DIGITS = 4

# Year range to skip — a 4-digit number like "2025" is a year, not an OTP
YEAR_MIN, YEAR_MAX = 2020, 2030

# Keywords that appear right before the OTP code in email bodies
OTP_LABELS = {"otp", "code", "verification"}


# ======================================================================
# Browser lifecycle
# ======================================================================


def create_browser() -> tuple:
    """Launch a persistent Chromium browser and return (playwright, context).

    The persistent context stores cookies in BROWSER_DATA_DIR so that
    login sessions survive across bot restarts.
    """
    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    browser = pw.chromium.launch_persistent_context(
        str(BROWSER_DATA_DIR),
        headless=True,
    )
    return pw, browser


# ======================================================================
# Login flow
# ======================================================================


def login(browser: BrowserContext, settings: Settings, telegram: Telegram) -> None:
    """Authenticate with IPL Fantasy, reusing cached session if possible.

    Flow:
      1. Check for existing auth cookie -> skip login if present
      2. Navigate to login page, enter email
      3. Wait for OTP form to appear
      4. Fetch OTP from email via IMAP
      5. Submit OTP and verify login succeeded
    """
    # Try to reuse an existing session before doing a full login
    if _has_cached_session(browser, telegram):
        return

    telegram.send_message("No cached session. Starting login flow...")
    page = browser.new_page()

    try:
        _submit_email(page, settings, telegram)
        _wait_for_otp_form(page)

        # Fetch OTP from email automatically
        telegram.send_message("OTP requested. Fetching from email...")
        otp = _fetch_otp_from_email(settings, telegram)
        if not otp:
            raise RuntimeError("Could not fetch OTP from email within timeout")

        _submit_otp(page, otp, telegram)
        _verify_login(page, browser, telegram)
    finally:
        page.close()


def _has_cached_session(browser: BrowserContext, telegram: Telegram) -> bool:
    """Check if a valid auth cookie exists from a previous session."""
    telegram.send_message("Checking for cached session...")
    cookies = _get_cookies(browser, "pre-login")

    if cookies.get("my11c-authToken"):
        telegram.send_message("Already logged in (cached session).")
        return True

    return False


def _submit_email(page, settings: Settings, telegram: Telegram) -> None:
    """Navigate to login page and submit the email address."""
    telegram.send_message("Loading login page...")
    page.goto(LOGIN_URL, wait_until="networkidle")

    telegram.send_message(f"Entering email: {settings.ipl_email}")
    page.fill("#email_input", settings.ipl_email)
    page.click("#registerCTA")
    page.wait_for_timeout(5000)


def _wait_for_otp_form(page) -> None:
    """Wait for the OTP input form to become visible after email submission.

    Raises RuntimeError with a debug screenshot if the form never appears.
    """
    # Check if the OTP form is already visible
    if page.locator("#paj-verifyform:not(.hide)").count() > 0:
        return

    try:
        page.wait_for_selector("#paj-verifyform:not(.hide)", timeout=10000)
    except Exception:
        page.screenshot(path=str(SCREENSHOT_DIR / "ipl_login_failed.png"))
        raise RuntimeError("OTP form never appeared after clicking Continue")


def _submit_otp(page, otp: str, telegram: Telegram) -> None:
    """Enter the OTP code and click verify."""
    telegram.send_message("Entering OTP and verifying...")
    page.fill("#otpInputField", otp)
    page.click("#verifyOtp")
    page.wait_for_timeout(8000)


def _verify_login(page, browser: BrowserContext, telegram: Telegram) -> None:
    """Confirm login succeeded by checking for auth cookie.

    Navigates to home page first if needed (some redirects don't
    set cookies until the home page is loaded).
    """
    if "/classic/home" not in page.url:
        page.goto(HOME_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

    cookies = _get_cookies(browser, "after-login")

    if cookies.get("my11c-authToken"):
        telegram.send_message("Login successful!")
    else:
        page.screenshot(path=str(SCREENSHOT_DIR / "ipl_login_failed.png"))
        telegram.send_message("Login may have failed. Check /tmp/ipl_login_failed.png")


# ======================================================================
# OTP extraction from email
# ======================================================================


def _fetch_otp_from_email(
    settings: Settings, telegram: Telegram, timeout: int = OTP_TIMEOUT
) -> str | None:
    """Poll Gmail IMAP inbox for an OTP email and extract the code.

    Checks every 3 seconds for new unread emails from IPL/My11Circle.
    Returns the OTP string, or None if timeout expires.
    """
    telegram.send_message("Checking email for OTP...")
    deadline = time.time() + timeout
    attempt = 0

    while time.time() < deadline:
        attempt += 1

        try:
            # Connect, search, extract, disconnect — each iteration
            otp = _check_inbox_for_otp(settings)
            if otp:
                log.info("OTP found: %s", otp)
                telegram.send_message("Got OTP from email.")
                return otp
        except Exception:
            log.exception("IMAP error")

        # Send periodic progress updates so user knows we're still trying
        if attempt % 5 == 0:
            remaining = int(deadline - time.time())
            telegram.send_message(
                f"Still waiting for OTP email... ({remaining}s remaining)"
            )

        time.sleep(3)

    telegram.send_message(f"Failed to get OTP within {timeout}s. Timed out.")
    log.error("Failed to fetch OTP within %ds", timeout)
    return None


def _check_inbox_for_otp(settings: Settings) -> str | None:
    """Connect to IMAP, scan unread emails, return OTP if found.

    Opens a fresh IMAP connection each time so we don't hold
    long-lived connections that may drop.
    """
    mail = imaplib.IMAP4_SSL(settings.imap_server, timeout=10)
    mail.login(settings.imap_email, settings.imap_password)
    mail.select("INBOX")

    try:
        _, msg_ids = mail.search(None, "UNSEEN")
        if not msg_ids[0]:
            return None

        # Check newest emails first (most likely to contain the OTP)
        for msg_id in reversed(msg_ids[0].split()):
            otp = _extract_otp_from_message(mail, msg_id)
            if otp:
                return otp

        return None
    finally:
        mail.logout()


def _extract_otp_from_message(mail, msg_id: bytes) -> str | None:
    """Fetch a single email and extract an OTP if it's from IPL/My11Circle.

    Returns the OTP string if found, None otherwise.
    """
    _, msg_data = mail.fetch(msg_id, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    # Only process emails that look like they're from IPL Fantasy
    if not _is_otp_email(msg):
        return None

    body = _get_email_body(msg)
    return _extract_otp_from_text(body)


def _is_otp_email(msg) -> bool:
    """Check if an email is likely an OTP message from IPL Fantasy.

    Matches on subject keywords (otp, verification) or sender
    domains (my11circle, iplt20).
    """
    subject = str(msg.get("Subject", "")).lower()
    sender = str(msg.get("From", "")).lower()

    has_otp_subject = "otp" in subject or "verification" in subject
    has_ipl_sender = "my11circle" in sender or "iplt20" in sender

    return has_otp_subject or has_ipl_sender


def _get_email_body(msg) -> str:
    """Extract the text body from an email message.

    For multipart emails, prefers text/plain over text/html.
    """
    if not msg.is_multipart():
        return msg.get_payload(decode=True).decode(errors="ignore")

    # Walk all parts, prefer plain text
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "text/plain":
            return part.get_payload(decode=True).decode(errors="ignore")
        if content_type == "text/html":
            # Fall back to HTML if no plain text part exists
            return part.get_payload(decode=True).decode(errors="ignore")

    return ""


def _extract_otp_from_text(text: str) -> str | None:
    """Find an OTP code in email body text (plain text or HTML).

    Uses regex to extract digit sequences since the email body may
    contain HTML tags that break simple word splitting (e.g.
    "<p>62458179 is your OTP</p>" — split() gives "<p>62458179").

    Strategy:
      1. Find a digit sequence near a label keyword ("OTP", "code",
         "verification"). Handles both orderings:
           - "OTP is 482910"
           - "62458179 is your OTP"
      2. Fall back to the first valid digit sequence in the text.

    Skips 4-digit numbers that look like years (2020-2030).
    """
    # Pull all digit sequences from the text (works through HTML tags)
    all_digits = re.findall(r"\d+", text)

    # Build a lowercase version of the text for label searching
    text_lower = text.lower()

    # Pass 1: Look for a valid digit sequence near a label keyword
    for label in OTP_LABELS:
        label_pos = text_lower.find(label)
        if label_pos == -1:
            continue

        # Check each digit sequence — prefer the one closest to the label
        for digits in all_digits:
            if not _is_valid_otp(digits):
                continue
            # Check that these digits appear near the label (within 50 chars)
            digit_pos = text.find(digits)
            if abs(digit_pos - label_pos) < 50:
                return digits

    # Pass 2: Fall back to first valid digit sequence in the text
    for digits in all_digits:
        if _is_valid_otp(digits):
            return digits

    return None


def _is_valid_otp(candidate: str) -> bool:
    """Check if a digit string is a plausible OTP.

    Must be at least 4 digits and not look like a calendar year.
    """
    if len(candidate) < OTP_MIN_DIGITS:
        return False

    # 4-digit numbers in the year range are almost certainly not OTPs
    if len(candidate) == 4 and YEAR_MIN <= int(candidate) <= YEAR_MAX:
        return False

    return True


# ======================================================================
# Helpers
# ======================================================================


def _get_cookies(browser: BrowserContext, label: str) -> dict:
    """Extract all cookies from the browser context as a dict.

    Also logs cookie names for debugging auth issues.
    """
    cookies = {c["name"]: c["value"] for c in browser.cookies()}
    log.info("[%s] Cookies (%d): %s", label, len(cookies), list(cookies.keys()))
    return cookies
