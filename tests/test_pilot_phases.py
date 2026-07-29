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
        _reviews_from_receipts({}, [receipt])

    assert error.value.code == "review_receipt_rejected"
    assert error.value.message == "review-1:review_evidence_unavailable"


def test_completed_review_receipt_without_verdict_is_invalid() -> None:
    receipt = {
        "task_id": "review-1",
        "status": "completed",
        "result": {"reason_hash": "0x" + "11" * 32},
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt])

    assert error.value.code == "review_receipt_invalid"
