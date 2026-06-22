from __future__ import annotations

from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.live_evidence import finalize_evidence_bundle
from loveengine_witness.live_protocol import build_live_event, build_live_session
from loveengine_witness.live_store import LocalArtifactStore, LiveMetadataStore


NOW = "1770000000"


def _store(tmp_path: Path) -> tuple[LiveMetadataStore, LocalArtifactStore]:
    return (
        LiveMetadataStore(tmp_path / "live.sqlite"),
        LocalArtifactStore(tmp_path / "artifacts"),
    )


def test_event_chain_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    metadata, artifacts = _store(tmp_path)
    session = build_live_session("session-1", "fixture", NOW)
    metadata.create_session(session)
    artifact_hash = artifacts.put(b"first line")
    event = build_live_event(
        event_id="event-1",
        session_id="session-1",
        sequence="1",
        occurred_at=NOW,
        category="source",
        source_type="fixture",
        content="first line",
        artifact_hash=artifact_hash,
        previous_event_hash="0x" + "00" * 32,
    )

    assert metadata.append_event(event)["duplicate"] is False
    assert metadata.append_event(event)["duplicate"] is True

    conflicting = dict(event)
    conflicting["content"] = "modified"
    with pytest.raises(LoveEngineError, match="different content") as exc:
        metadata.append_event(conflicting)
    assert exc.value.code == "event_conflict"

    gap = build_live_event(
        event_id="event-3",
        session_id="session-1",
        sequence="3",
        occurred_at=NOW,
        category="source",
        source_type="fixture",
        content="gap",
        artifact_hash=artifacts.put(b"gap"),
        previous_event_hash=event["event_hash"],
    )
    with pytest.raises(LoveEngineError) as exc:
        metadata.append_event(gap)
    assert exc.value.code == "sequence_gap"


def test_finalize_is_stable_and_closed_session_is_immutable(tmp_path: Path) -> None:
    metadata, artifacts = _store(tmp_path)
    metadata.create_session(build_live_session("session-2", "fixture", NOW))
    previous = "0x" + "00" * 32
    for index, text in enumerate(("one", "two"), start=1):
        event = build_live_event(
            event_id=f"event-{index}",
            session_id="session-2",
            sequence=str(index),
            occurred_at=str(int(NOW) + index),
            category="source",
            source_type="fixture",
            content=text,
            artifact_hash=artifacts.put(text.encode()),
            previous_event_hash=previous,
        )
        metadata.append_event(event)
        previous = event["event_hash"]

    metadata.close_session("session-2", str(int(NOW) + 10))
    first = finalize_evidence_bundle(
        metadata,
        artifacts,
        "session-2",
        revision="1",
        finalized_at=str(int(NOW) + 20),
    )
    second = finalize_evidence_bundle(
        metadata,
        artifacts,
        "session-2",
        revision="1",
        finalized_at=str(int(NOW) + 20),
    )
    assert first["bundle_hash"] == second["bundle_hash"]
    assert first["event_count"] == "2"

    with pytest.raises(LoveEngineError) as exc:
        metadata.append_event(
            build_live_event(
                event_id="late",
                session_id="session-2",
                sequence="3",
                occurred_at=str(int(NOW) + 30),
                category="source",
                source_type="fixture",
                content="late",
                artifact_hash=artifacts.put(b"late"),
                previous_event_hash=previous,
            )
        )
    assert exc.value.code == "session_closed"

    artifact_path = artifacts.path_for(first["events"][0]["artifact_hash"])
    artifact_path.unlink()
    with pytest.raises(LoveEngineError) as exc:
        finalize_evidence_bundle(
            metadata,
            artifacts,
            "session-2",
            revision="2",
            finalized_at=str(int(NOW) + 21),
        )
    assert exc.value.code == "artifact_missing"
