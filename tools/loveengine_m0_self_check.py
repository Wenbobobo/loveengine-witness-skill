#!/usr/bin/env python3
"""Run the LoveEngine Witness Skill M0 capability self-check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_loveengine_m0.py"
NODE_PROFILE = ROOT / "skills" / "loveengine-witness" / "fixtures" / "agent-node-profile.fixture.json"
MANIFEST = ROOT / "skills" / "loveengine-witness" / "skill-manifest.m0.json"


def main() -> int:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.stdout.write(result.stdout)
        return result.returncode

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    node = json.loads(NODE_PROFILE.read_text(encoding="utf-8"))
    summary = {
        "skill_id": manifest["skill_id"],
        "skill_version": manifest["version"],
        "package_hash": manifest["package_hash"],
        "node_id": node["node_id"],
        "agent_runtime": node["agent_runtime"],
        "signer_type": node["signer_type"],
        "private_key_available_to_agent": node["private_key_available_to_agent"],
        "capabilities": node["capabilities"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
