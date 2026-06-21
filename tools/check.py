#!/usr/bin/env python3
"""Run all repository checks for LoveEngine Witness Skill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str]) -> None:
    print("+ " + " ".join(args))
    result = subprocess.run(args, cwd=ROOT, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    python = sys.executable
    run([python, "tools/validate_loveengine_m0.py"])
    run([python, "tools/validate_loveengine_m0.py", "--tamper-check"])
    run([python, "tools/loveengine_m0_self_check.py"])
    run([python, "tools/validate_sources.py"])
    run([python, "tools/validate_docs.py"])


if __name__ == "__main__":
    main()
