from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_VALIDATE_DOCS_SPEC = importlib.util.spec_from_file_location(
    "loveengine_test_validate_docs", ROOT / "tools" / "validate_docs.py"
)
assert _VALIDATE_DOCS_SPEC is not None and _VALIDATE_DOCS_SPEC.loader is not None
validate_docs = importlib.util.module_from_spec(_VALIDATE_DOCS_SPEC)
_VALIDATE_DOCS_SPEC.loader.exec_module(validate_docs)

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


def test_current_spec_and_preserved_release_assets_exist() -> None:
    spec = ROOT / "docs/specs/love-engine-pre-enterprise-remote-lab.md"
    assert "Pre-enterprise Remote Lab" in spec.read_text(encoding="utf-8")
    implemented = (
        ROOT
        / "docs/archive/specs/implemented/love-engine-witness-core-optimization.md"
    )
    assert "Witness Core Optimization" in implemented.read_text(encoding="utf-8")

    assert (
        ROOT
        / "docs/archive/planning/2026-06-24/m6-demo-docs-publication-plan.md"
    ).is_file()
    assert (ROOT / "docs/articles/loveengine-technical-architecture.zh-CN.md").is_file()

    for rel_path in M6_8_RUNBOOKS:
        assert (ROOT / rel_path).is_file(), rel_path

    for rel_path in M6_8_SCREENSHOTS:
        path = ROOT / rel_path
        assert path.is_file(), rel_path
        assert path.stat().st_size > 1024, rel_path


def test_release_gate_runs_accelerated_soak_in_temporary_output() -> None:
    script = (ROOT / "tools/run_release_checks.ps1").read_text(encoding="utf-8")

    assert 'Invoke-CheckedNative "contract preparation"' in script
    assert "loveengine pilot contracts prepare" in script
    assert 'Invoke-CheckedNative "accelerated soak"' in script
    assert "loveengine pilot soak" in script
    assert "--duration-seconds 1" in script
    assert '--output (Join-Path $buildCheckRoot "accelerated-soak")' in script


def test_documentation_allows_only_declared_generated_contract_runtime_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repository"
    document = root / "docs" / "fixture.md"
    document.parent.mkdir(parents=True)
    document.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(validate_docs, "ROOT", root)
    errors: list[str] = []

    validate_docs.validate_inline_paths(
        document,
        "`contracts/lib` `contracts/out/WitnessDAO.sol/WitnessDAO.json` "
        "`contracts/cache/loveengine-contract-preparation.json`",
        errors,
    )
    validate_docs.validate_inline_paths(document, "`contracts/unknown`", errors)

    assert errors == ["missing inline path in docs/fixture.md: contracts/unknown"]


def test_ci_refreshes_pinned_contract_dependencies() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count(
        "uv run loveengine pilot contracts prepare --refresh-dependencies"
    ) == 5
