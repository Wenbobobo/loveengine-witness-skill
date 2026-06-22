from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveengine_witness.live_demo import run_live_evidence_demo
from loveengine_witness.live_transcript import verify_live_transcript


@pytest.mark.integration
def test_live_evidence_demo_is_auditable_and_fail_closed(tmp_path: Path) -> None:
    result = run_live_evidence_demo(tmp_path)
    assert result["event_count"] == 12
    assert result["authenticated_nodes"] == 3
    assert result["review_processes"] == 3
    assert result["accepted_gate"] is True
    assert result["blocked_gate"] is True
    assert result["metrics"]["acked"] == 5
    assert result["metrics"]["connected"] == 0

    transcript = json.loads(Path(result["transcript_path"]).read_text(encoding="utf-8"))
    verified = verify_live_transcript(transcript)
    assert verified["valid"] is True
    assert transcript["disputes"][0]["status"] == "dismissed"
    assert transcript["disputes"][1]["status"] == "unresolved"
