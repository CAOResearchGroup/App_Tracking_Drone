"""Run from a source checkout without installing the package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from multi_drone.ui import main

    raise SystemExit(main())
