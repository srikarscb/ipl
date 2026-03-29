"""Telegram bot integration for sending notifications and receiving user input.

All user-facing communication flows through this module — the bot sends
status updates, screenshots, and team info, and receives transfer/captain
instructions from the user via Telegram messages.
"""

import logging
import time

import httpx

from ipl_fantasy.config import Settings

log = logging.getLogger(__name__)

# Telegram Bot API base URL — {token} is replaced at runtime
BASE_URL = "https://api.telegram.org/bot{token}"


class Telegram:
    """Handles all communication with the Telegram Bot API."""

    def __init__(self, settings: Settings) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.client = httpx.Client(timeout=30)
        # Tracks the last processed update so we don't re-read old messages
        self._last_update_id: int | None = None

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    def send_message(self, text: str, parse_mode: str | None = None) -> None:
        """Send a text message to the configured Telegram chat.

        If parse_mode (e.g. "Markdown") is set and the API rejects it
        (often due to special characters), retries without formatting.
        """
        url = self._api_url("sendMessage")
        payload: dict = {"chat_id": self.chat_id, "text": text}

        if parse_mode:
            payload["parse_mode"] = parse_mode

        resp = self.client.post(url, json=payload)

        # Retry without formatting if the parse mode caused a failure
        if not resp.is_success and parse_mode:
            payload.pop("parse_mode")
            resp = self.client.post(url, json=payload)

        if not resp.is_success:
            log.error("Telegram send failed: %s", resp.text)
            return

        log.info("Telegram message sent")

    def send_photo(self, photo_path: str, caption: str = "") -> None:
        """Send a screenshot image to the configured Telegram chat."""
        url = self._api_url("sendPhoto")

        with open(photo_path, "rb") as f:
            resp = self.client.post(
                url,
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": ("screenshot.png", f, "image/png")},
            )

        if not resp.is_success:
            log.error("Telegram photo send failed: %s", resp.text)
            return

        log.info("Telegram photo sent")

    # ------------------------------------------------------------------
    # Receiving messages
    # ------------------------------------------------------------------

    def wait_for_reply(self, timeout: int = 120) -> str | None:
        """Block until the user sends a message, or timeout expires.

        Returns the message text, or None if no reply within timeout.
        """
        # Discard any messages that arrived before we started waiting
        self._flush_old_updates()

        # Poll for the user's next message
        return self._poll_for_message(timeout)

    def _flush_old_updates(self) -> None:
        """Consume and discard all pending Telegram updates.

        This prevents the bot from acting on stale messages that were
        sent before the current prompt was shown to the user.
        """
        url = self._api_url("getUpdates")
        params: dict = {"timeout": 0}

        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        resp = self.client.get(url, params=params)
        if resp.is_success:
            results = resp.json().get("result", [])
            if results:
                # Move the cursor past all existing updates
                self._last_update_id = results[-1]["update_id"]

    def _poll_for_message(self, timeout: int) -> str | None:
        """Long-poll Telegram for a new message from our chat.

        Uses 5-second long-poll intervals so the API holds the
        connection open rather than us busy-looping.
        """
        url = self._api_url("getUpdates")
        deadline = time.time() + timeout

        while time.time() < deadline:
            params: dict = {"timeout": 5}
            if self._last_update_id is not None:
                params["offset"] = self._last_update_id + 1

            resp = self.client.get(url, params=params)
            if not resp.is_success:
                time.sleep(2)
                continue

            # Check each update for a message from our chat
            for update in resp.json().get("result", []):
                self._last_update_id = update["update_id"]
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                    return msg.get("text", "").strip()

            time.sleep(2)

        log.warning("Telegram reply timed out after %ds", timeout)
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _api_url(self, method: str) -> str:
        """Build the full Telegram Bot API URL for a given method."""
        return f"{BASE_URL.format(token=self.token)}/{method}"
