#!/usr/bin/env python3
"""Create and enqueue one signed task inside a loopback-only Pilot lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loveengine_witness.pilot_task_operator import enqueue_review_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile-index", type=int, default=1)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--dispute-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = enqueue_review_task(
        args.root,
        profile_index=args.profile_index,
        task_id=args.task_id,
        dispute_id=args.dispute_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["queued"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
