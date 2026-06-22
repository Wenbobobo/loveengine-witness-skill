from __future__ import annotations

import pytest

from loveengine_witness.dispute import (
    aggregate_reviews,
    build_dispute,
    build_proposal_plan,
    build_review,
)
from loveengine_witness.errors import LoveEngineError


NODES = [
    "0x0000000000000000000000000000000000000001",
    "0x0000000000000000000000000000000000000002",
    "0x0000000000000000000000000000000000000003",
]
BUNDLE = "0x" + "11" * 32


def test_three_distinct_reviews_resolve_by_majority() -> None:
    dispute = build_dispute(
        "dispute-1", BUNDLE, "critical", "0x" + "22" * 32, "1770000100"
    )
    reviews = [
        build_review("review-1", dispute, NODES[0], "dismiss", "0x" + "31" * 32, "1"),
        build_review("review-2", dispute, NODES[1], "dismiss", "0x" + "32" * 32, "2"),
        build_review("review-3", dispute, NODES[2], "uphold", "0x" + "33" * 32, "3"),
    ]
    result = aggregate_reviews(dispute, reviews, expected_nodes=set(NODES))
    assert result["status"] == "dismissed"
    assert result["valid_review_count"] == "3"


def test_missing_or_duplicate_review_is_unresolved() -> None:
    dispute = build_dispute(
        "dispute-2", BUNDLE, "critical", "0x" + "22" * 32, "1770000100"
    )
    reviews = [
        build_review("review-1", dispute, NODES[0], "dismiss", "0x" + "31" * 32, "1"),
        build_review("review-2", dispute, NODES[1], "dismiss", "0x" + "32" * 32, "2"),
    ]
    assert aggregate_reviews(dispute, reviews, expected_nodes=set(NODES))[
        "status"
    ] == "unresolved"
    with pytest.raises(LoveEngineError) as exc:
        aggregate_reviews(dispute, reviews + [dict(reviews[0])], expected_nodes=set(NODES))
    assert exc.value.code == "duplicate_review_node"


def test_proposal_gate_is_read_only_and_fail_closed() -> None:
    plan = build_proposal_plan(
        session={"session_id": "session-1", "status": "closed"},
        bundle={
            "session_id": "session-1",
            "status": "finalized",
            "bundle_hash": BUNDLE,
        },
        disputes=[{"dispute_id": "d1", "severity": "critical", "status": "dismissed"}],
        proposal={"action": "set_total_uto", "value": "100"},
    )
    assert plan["ready"] is True
    assert plan["proposal_hash"].startswith("0x")

    with pytest.raises(LoveEngineError) as exc:
        build_proposal_plan(
            session={"session_id": "session-1", "status": "closed"},
            bundle={
                "session_id": "session-1",
                "status": "finalized",
                "bundle_hash": BUNDLE,
            },
            disputes=[
                {"dispute_id": "d2", "severity": "critical", "status": "unresolved"}
            ],
            proposal={"action": "set_total_uto", "value": "100"},
        )
    assert exc.value.code == "proposal_gate_blocked"
