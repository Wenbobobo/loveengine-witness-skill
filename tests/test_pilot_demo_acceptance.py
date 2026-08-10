from __future__ import annotations

import json
from pathlib import Path

import pytest

import loveengine_witness.pilot_demo as pilot_demo
from loveengine_witness.errors import LoveEngineError


def _result(tmp_path: Path) -> dict[str, object]:
    return {
        "transcript": {
            "acceptance": {
                "duration_seconds": 900,
                "event_count": 30,
                "observer_count": 10,
                "restart_verified": True,
                "reconnect_verified": True,
                "cleanup_verified": True,
                "secret_findings": 0,
                "stderr_empty": True,
            },
            "transcript_hash": "sha256:" + "0" * 64,
        },
        "transcript_path": str(tmp_path / "witness-core.fixture.json"),
    }


def test_finalize_v2_standalone_abstains_from_external_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "operator.token"
    token_file.write_text("restricted-test-token", encoding="utf-8")
    result = _result(tmp_path)
    monkeypatch.setattr(
        pilot_demo, "core_transcript_hash", lambda value: "sha256:" + "1" * 64
    )
    monkeypatch.setattr(
        pilot_demo,
        "verify_core_transcript",
        lambda value: {"verification_level": "offline_integrity"},
    )

    pilot_demo._finalize_v2_standalone_transcript(
        result,
        output=tmp_path,
        token_file=token_file,
        chain_process_exited=True,
    )

    acceptance = result["transcript"]["acceptance"]
    assert acceptance["cleanup_verified"] is False
    assert acceptance["secret_findings"] == 0
    assert acceptance["stderr_empty"] is False
    assert result["acceptance_verified"] is False
    assert result["offline_verification"] == "standalone_unverified"
    assert result["integrity_verification"] == "offline_integrity"
    assert result["acceptance_evidence"] == {
        "verification_scope": "standalone_process",
        "reason": "external_process_evidence_required",
        "pilot_write_token_scan_completed": True,
        "pilot_write_token_findings": 0,
        "chain_process_exited": True,
        "complete_process_tree_cleanup_verified": False,
        "outer_process_stderr_observed": False,
    }
    assert json.loads(
        (tmp_path / "witness-core.fixture.json").read_text(encoding="utf-8")
    ) == result["transcript"]


def test_finalize_v2_standalone_persists_findings_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "operator.token"
    token_file.write_text("restricted-test-token", encoding="utf-8")
    (tmp_path / "leak.txt").write_text(
        "prefix restricted-test-token suffix", encoding="utf-8"
    )
    result = _result(tmp_path)
    monkeypatch.setattr(
        pilot_demo, "core_transcript_hash", lambda value: "sha256:" + "2" * 64
    )
    monkeypatch.setattr(
        pilot_demo,
        "verify_core_transcript",
        lambda value: pytest.fail("invalid evidence must not reach verifier"),
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_demo._finalize_v2_standalone_transcript(
            result,
            output=tmp_path,
            token_file=token_file,
            chain_process_exited=False,
        )

    assert error.value.code == "pilot_secret_findings"
    written = json.loads(
        (tmp_path / "witness-core.fixture.json").read_text(encoding="utf-8")
    )
    assert written["acceptance"]["secret_findings"] == 1
    assert written["acceptance"]["cleanup_verified"] is False
    assert written["acceptance"]["stderr_empty"] is False
    assert result["acceptance_verified"] is False
    assert result["acceptance_evidence"]["pilot_write_token_findings"] == 1


def test_scan_pilot_token_leaks_excludes_source_and_is_stable(tmp_path: Path) -> None:
    token_file = tmp_path / "operator.token"
    token_file.write_text("restricted-test-token", encoding="utf-8")
    (tmp_path / "z.txt").write_text("restricted-test-token", encoding="utf-8")
    (tmp_path / "a.txt").write_text("restricted-test-token", encoding="utf-8")

    assert pilot_demo._scan_pilot_token_leaks(tmp_path, token_file) == [
        "a.txt",
        "z.txt",
    ]
