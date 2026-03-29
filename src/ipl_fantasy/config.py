"""Application settings loaded from environment variables / .env file.

All credentials and configuration are read via pydantic-settings,
which automatically maps environment variable names to field names.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for the IPL Fantasy Bot.

    Attributes:
        ipl_email: Email address registered on fantasy.iplt20.com.
        imap_server: IMAP server for fetching OTP emails.
        imap_email: Email account to read OTP emails from.
        imap_password: App password for the IMAP email account.
        telegram_bot_token: Bot token from @BotFather for notifications.
        telegram_chat_id: Telegram chat ID to send messages to / receive from.
    """

    ipl_email: str
    imap_server: str = "imap.gmail.com"
    imap_email: str
    imap_password: str
    telegram_bot_token: str
    telegram_chat_id: str

    model_config = {"env_file": ".env"}
