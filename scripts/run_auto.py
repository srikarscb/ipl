"""Cron entry point for auto-swapping players not in the playing XI.

Run after the toss (typically ~30 min before match start):
  uv run python scripts/run_auto.py
"""

import sys
from pathlib import Path

# Add src to path so we can import ipl_fantasy
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ipl_fantasy.bot import run_auto

if __name__ == "__main__":
    run_auto()
