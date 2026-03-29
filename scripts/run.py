import sys
from pathlib import Path

# Add src to path so we can import ipl_fantasy
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ipl_fantasy.bot import run

if __name__ == "__main__":
    run()
