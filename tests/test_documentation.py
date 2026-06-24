from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

M6_8_RUNBOOKS = [
    "docs/development/runbooks/operator-flow.zh-CN.md",
    "docs/development/runbooks/observation-node-flow.zh-CN.md",
    "docs/development/runbooks/voting-witness-flow.zh-CN.md",
    "docs/development/runbooks/public-viewer-flow.zh-CN.md",
    "docs/development/runbooks/publisher-flow.zh-CN.md",
]

M6_8_SCREENSHOTS = [
    "docs/assets/runbooks/operator/operator-01-open-console-zh.png",
    "docs/assets/runbooks/operator/operator-02-create-session-zh.png",
    "docs/assets/runbooks/operator/operator-03-publish-event-zh.png",
    "docs/assets/runbooks/operator/operator-04-close-session-zh.png",
    "docs/assets/runbooks/viewer/viewer-01-dashboard-zh.png",
    "docs/assets/runbooks/viewer/viewer-02-evidence-detail-zh.png",
    "docs/assets/runbooks/viewer/viewer-03-gate-status-zh.png",
]


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


def test_m6_8_release_closeout_documentation_assets_exist() -> None:
    spec = ROOT / "docs/specs/love-engine-contract-public-pilot-spec.md"
    assert "M6.8 Release closeout" in spec.read_text(encoding="utf-8")

    assert (ROOT / "docs/development/m6-demo-docs-publication-plan.md").is_file()
    assert (ROOT / "docs/articles/loveengine-technical-architecture.zh-CN.md").is_file()

    for rel_path in M6_8_RUNBOOKS:
        assert (ROOT / rel_path).is_file(), rel_path

    for rel_path in M6_8_SCREENSHOTS:
        path = ROOT / rel_path
        assert path.is_file(), rel_path
        assert path.stat().st_size > 1024, rel_path
