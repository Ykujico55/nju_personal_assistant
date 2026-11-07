"""Run from the owner's terminal; only this extension's source is added."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from plugin_developer.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
