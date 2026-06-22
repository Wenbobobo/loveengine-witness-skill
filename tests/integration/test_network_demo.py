from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveengine_witness.network_demo import run_network_demo
from loveengine_witness.network_transcript import verify_network_transcript


@pytest.mark.integration
def test_three_node_network_demo_is_auditable(tmp_path: Path) -> None:
    result = run_network_demo(tmp_path, nodes=3)

    assert result["authenticated_nodes"] == 3
    assert result["node_processes"] == 3
    assert result["task_types"] == ["observe_broadcast", "propagate_skill"]
    assert result["metrics"]["acked"] == 6
    assert result["metrics"]["rejected"] >= 3

    transcript = json.loads(Path(result["transcript_path"]).read_text())
    verified = verify_network_transcript(transcript)
    assert verified["valid"] is True
    assert len(transcript["nodes"]) == 3
    assert len(transcript["receipts"]) == 6
    assert transcript["recovery"]["offline_redelivery"] is True
    assert transcript["recovery"]["duplicate_suppressed"] is True
    assert transcript["release"]["status"] == "deprecated"
