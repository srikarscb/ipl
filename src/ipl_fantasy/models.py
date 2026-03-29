"""Data models for IPL Fantasy player representation.

These models provide type-safe structures for passing player data
between the UI driver, bot logic, and Telegram notifications.
"""

from pydantic import BaseModel


class Player(BaseModel):
    """Represents a single player in the fantasy team.

    Attributes:
        name: Player's display name as shown on the fantasy site.
        team: IPL franchise abbreviation (e.g., "RCB", "CSK").
        role: Positional role tab the player appears under
              (WK, BAT, AR, BOWL).
        is_captain: Whether this player is the current captain.
        is_vice_captain: Whether this player is the current vice-captain.
    """

    name: str
    team: str
    role: str = ""
    is_captain: bool = False
    is_vice_captain: bool = False

    def __str__(self) -> str:
        """Format as 'Name (Team) [C]/[VC]' for display."""
        tag = " [C]" if self.is_captain else " [VC]" if self.is_vice_captain else ""
        return f"{self.name} ({self.team}){tag}"
