#!/usr/bin/env python3
"""실행 진입점. 사용법은 README.md 참고.

    python slate_rename.py <촬영폴더> [--dry-run] [--watch] [--ok]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slate_rename.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
