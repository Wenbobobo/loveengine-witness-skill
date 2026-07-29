from __future__ import annotations

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_phases import _reviews_from_receipts


def test_rejected_review_receipt_has_a_stable_pilot_error() -> None:
    receipt = {
        "task_id": "review-1",
        "status": "rejected",
        "result": {"error_code": "review_evidence_unavailable"},
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {})

    assert error.value.code == "review_receipt_rejected"
    assert error.value.message == "review-1:review_evidence_unavailable"


def test_completed_review_receipt_without_verdict_is_invalid() -> None:
    receipt = {
        "task_id": "review-1",
        "status": "completed",
        "result": {"reason_hash": "0x" + "11" * 32},
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {})

    assert error.value.code == "review_receipt_invalid"


def test_completed_review_receipt_requires_verified_evidence() -> None:
    task_id = "review-1"
    task = {
        "payload": {
            "schema_version": "loveengine.review-dispute-payload/1",
            "dispute_id": "dispute-1",
            "bundle_hash": "0x" + "11" * 32,
            "session_id": "session-1",
            "evidence_url": "http://127.0.0.1:8780/evidence",
            "events_url": "http://127.0.0.1:8780/events",
            "artifact_base_url": "http://127.0.0.1:8780/artifacts",
            "revision": "1",
            "event_count": "1",
            "head_event_hash": "0x" + "22" * 32,
        }
    }
    receipt = {
        "task_id": task_id,
        "status": "completed",
        "result": {
            "dispute_id": "dispute-1",
            "bundle_hash": "0x" + "11" * 32,
            "session_id": "session-1",
            "revision": "1",
            "event_count": "1",
            "head_event_hash": "0x" + "22" * 32,
            "evidence_verified": False,
            "verdict": "dismiss",
            "reason_hash": "0x" + "33" * 32,
        },
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {task_id: task})

    assert error.value.code == "review_evidence_not_verified"
