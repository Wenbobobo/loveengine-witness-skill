from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loveengine_witness.demo import free_port
from loveengine_witness.pilot_chain import (
    initialize_chain,
    restore_chain,
    snapshot_chain,
    start_chain,
    status_chain,
    stop_chain,
)


@pytest.mark.integration
def test_persistent_chain_restores_addresses_code_and_state(tmp_path: Path) -> None:
    port = free_port()
    initialized = initialize_chain(tmp_path, port=port)
    assert initialized["chain_id"] == "31337"
    assert set(initialized["contracts"]) == {
        "StreamingEngine",
        "PublicSink",
        "CorporateSink",
        "WitnessDAO",
        "SkillRegistry",
    }

    process = start_chain(tmp_path, port=port)
    try:
        rpc_url = f"http://127.0.0.1:{port}"
        status = status_chain(tmp_path, rpc_url)
        assert status["valid"] is True
        first_snapshot = snapshot_chain(tmp_path, rpc_url)
        assert Path(first_snapshot["snapshot"]).is_file()
    finally:
        stop_chain(tmp_path, process)

    restarted = start_chain(tmp_path, port=port)
    try:
        status = status_chain(tmp_path, f"http://127.0.0.1:{port}")
        assert status["valid"] is True
        restored = restore_chain(
            tmp_path,
            f"http://127.0.0.1:{port}",
            Path(first_snapshot["snapshot"]),
        )
        assert restored["restored"] is True
    finally:
        stop_chain(tmp_path, restarted)


def test_chain_start_refuses_missing_initialization(tmp_path: Path) -> None:
    with pytest.raises(Exception) as exc:
        start_chain(tmp_path, port=free_port())
    assert getattr(exc.value, "code", None) == "chain_not_initialized"
