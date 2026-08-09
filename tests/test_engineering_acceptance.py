from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import run_engineering_acceptance as acceptance  # noqa: E402


def _terminal_evidence() -> tuple[dict, dict, dict]:
    status = {
        "status": "passed",
        "process_alive": False,
        "run_id": "run-1",
    }
    report = {
        "run_id": "run-1",
        "passed": True,
        "failure": None,
        "requested_duration_seconds": 900,
        "elapsed_seconds": 900.0,
        "event_count": 30,
        "observer_count": 10,
        "core_transcript_version": 2,
        "checks": {
            name: True for name in acceptance.V2_REQUIRED_SOAK_CHECKS
        },
        "secret_leaks": [],
    }
    verification = {
        "valid": True,
        "verification_level": "offline_integrity",
        "chain_verified": False,
        "trust_bound": False,
        "run_id": "run-1",
        "event_count": 30,
        "observation_receipts": 3,
        "review_receipts": 3,
        "gate_ready": True,
        "participant_claims_verified": True,
        "nodes": 3,
        "declared_operator_groups": 2,
        "declared_network_groups": 2,
    }
    return status, report, verification


def test_engineering_acceptance_profile_is_fixed_and_honest() -> None:
    report = acceptance._initial_report(
        {"commit": "a" * 40, "branch": "candidate", "clean": "true"},
        "sha256:" + "b" * 64,
    )

    assert report["evidence_class"] == "engineering_acceptance"
    assert report["duration_seconds"] == 900
    assert report["event_count"] == 30
    assert report["observer_count"] == 10
    assert report["core_transcript_version"] == 2
    assert report["long_term_availability_verified"] is False
    assert report["production_ready"] is False
    assert report["environment"] == "local_anvil"
    assert report["actors_simulated"] is True
    assert report["manifest_package_hash"] == "sha256:" + "b" * 64
    assert "package_hash" not in report


def test_terminal_evidence_requires_empty_runtime_stderr() -> None:
    status, report, verification = _terminal_evidence()

    with pytest.raises(acceptance.AcceptanceError, match="unexpected_stderr"):
        acceptance._validate_terminal_evidence(
            status,
            report,
            verification,
            diagnostic_sizes={"soak_process": 1},
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("requested_duration_seconds", 899, "soak_duration_mismatch"),
        ("event_count", 29, "soak_event_count_mismatch"),
        ("observer_count", 9, "soak_observer_count_mismatch"),
    ],
)
def test_terminal_evidence_rejects_profile_downgrade(
    field: str, value: int, error: str
) -> None:
    status, report, verification = _terminal_evidence()
    report[field] = value

    with pytest.raises(acceptance.AcceptanceError, match=error):
        acceptance._validate_terminal_evidence(
            status, report, verification, diagnostic_sizes={"soak_process": 0}
        )


def test_terminal_evidence_accepts_complete_profile() -> None:
    status, report, verification = _terminal_evidence()

    acceptance._validate_terminal_evidence(
        status,
        report,
        verification,
        diagnostic_sizes={
            "soak_launch": 0,
            "soak_process": 0,
            "transcript_verify": 0,
        },
    )


def test_soak_transcript_path_preserves_failure_and_rejects_null(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        acceptance.AcceptanceError,
        match="soak_report_failed:invalid_participant_attestation",
    ):
        acceptance._soak_transcript_path(
            {
                "passed": False,
                "failure": {"code": "invalid_participant_attestation"},
                "transcript_path": None,
            },
            tmp_path,
        )

    with pytest.raises(
        acceptance.AcceptanceError, match="soak_transcript_path_missing"
    ):
        acceptance._soak_transcript_path(
            {"passed": True, "failure": None, "transcript_path": None},
            tmp_path,
        )


def test_terminal_evidence_rejects_missing_check_or_short_elapsed() -> None:
    status, report, verification = _terminal_evidence()
    report["checks"].pop("v2_wall_clock_duration_met")

    with pytest.raises(
        acceptance.AcceptanceError, match="soak_check_inventory_incomplete"
    ):
        acceptance._validate_terminal_evidence(
            status, report, verification, diagnostic_sizes={"soak_process": 0}
        )

    status, report, verification = _terminal_evidence()
    report["elapsed_seconds"] = 899.999
    with pytest.raises(
        acceptance.AcceptanceError, match="soak_wall_clock_duration_not_met"
    ):
        acceptance._validate_terminal_evidence(
            status, report, verification, diagnostic_sizes={"soak_process": 0}
        )


def test_cleanup_refuses_mismatched_run_without_touching_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soak_output = (tmp_path / "soak").resolve()
    soak_output.mkdir()
    state_path = soak_output / "pilot-soak-run.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "run_id": "different-run",
                "output": str(soak_output),
                "package_root": str(acceptance.ROOT),
                "started_at_epoch": 1,
            }
        ),
        encoding="utf-8",
    )

    def unexpected_process(_pid: int) -> None:
        raise AssertionError("mismatched state must not query or terminate a process")

    monkeypatch.setattr(acceptance.psutil, "Process", unexpected_process)
    cleanup = acceptance._cleanup_owned_soak(
        {"pid": 123, "run_id": "expected-run"}, state_path, soak_output
    )

    assert cleanup == {
        "attempted": True,
        "identity_verified": False,
        "stopped": False,
        "error": "cleanup_state_identity_mismatch",
    }
