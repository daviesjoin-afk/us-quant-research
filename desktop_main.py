"""Stable desktop entry point for source-tree and packaged launches."""

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if SOURCE_ROOT.is_dir() and str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from us_quant.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
