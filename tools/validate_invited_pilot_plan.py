#!/usr/bin/env python3
"""Validate an invited Sepolia pilot YAML plan with secret-file references."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.invited_pilot_plan import load_invited_pilot_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--check-input-files", action="store_true")
    args = parser.parse_args()
    try:
        _, summary = load_invited_pilot_plan(
            args.plan,
            check_input_files=args.check_input_files,
        )
    except LoveEngineError as exc:
        print(
            json.dumps(
                {"valid": False, "error": {"code": exc.code, "message": exc.message}},
                sort_keys=True,
            )
        )
        return exc.exit_code
    print(json.dumps({"valid": True, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
