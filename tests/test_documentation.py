from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_documentation_has_valid_links_and_no_stale_workspace_paths() -> None:
    result = subprocess.run(
        [sys.executable, "tools/validate_docs.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LoveEngine documentation validation passed" in result.stdout
