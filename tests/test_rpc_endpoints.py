from __future__ import annotations

from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.rpc_endpoints import (
    resolve_rpc_endpoint,
    validate_rpc_pair,
)


def test_sepolia_rpc_requires_restricted_file() -> None:
    with pytest.raises(LoveEngineError) as error:
        resolve_rpc_endpoint(
            direct_url="https://rpc.example.invalid/key",
            url_file=None,
            chain_id="11155111",
            label="primary",
            required=True,
        )

    assert error.value.code == "rpc_url_file_required"


def test_sepolia_rpc_file_is_resolved_without_echoing_value(tmp_path: Path) -> None:
    rpc_file = tmp_path / "sepolia-rpc.url"
    rpc_file.write_text("https://rpc-a.example.invalid/key\n", encoding="utf-8")
    rpc_file.chmod(0o600)

    value = resolve_rpc_endpoint(
        direct_url=None,
        url_file=rpc_file,
        chain_id="11155111",
        label="primary",
        required=True,
    )

    assert value == "https://rpc-a.example.invalid/key"


def test_sepolia_rpc_pair_requires_distinct_hostnames() -> None:
    with pytest.raises(LoveEngineError) as error:
        validate_rpc_pair(
            "https://rpc.example.invalid/a",
            "https://rpc.example.invalid/b",
            chain_id="11155111",
            require_secondary=True,
        )

    assert error.value.code == "distinct_rpc_origins_required"


def test_sepolia_rpc_pair_accepts_distinct_hostnames() -> None:
    validate_rpc_pair(
        "https://rpc-a.example.invalid/key",
        "https://rpc-b.example.invalid/key",
        chain_id="11155111",
        require_secondary=True,
    )


def test_local_anvil_rpc_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(LoveEngineError) as error:
        resolve_rpc_endpoint(
            direct_url="http://192.0.2.10:8545",
            url_file=None,
            chain_id="31337",
            label="primary",
            required=True,
        )

    assert error.value.code == "rpc_endpoint_invalid"
