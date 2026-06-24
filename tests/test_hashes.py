from __future__ import annotations

from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.hashes import (
    keccak256_hex,
    sha256_prefixed,
    source_sha256_prefixed,
)


def test_canonical_json_v1_is_utf8_sorted_and_compact() -> None:
    value = {"b": 2, "a": "爱"}

    assert canonical_json_bytes(value) == '{"a":"爱","b":2}'.encode()


def test_sha256_uses_prefixed_lowercase_hex() -> None:
    assert (
        sha256_prefixed(b"")
        == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_source_sha256_normalizes_text_line_endings(tmp_path) -> None:
    crlf_path = tmp_path / "source.json"
    lf_path = tmp_path / "source.md"
    crlf_path.write_bytes(b'{\r\n  "name": "loveengine"\r\n}\r\n')
    lf_path.write_bytes(b'{\n  "name": "loveengine"\n}\n')

    assert source_sha256_prefixed(crlf_path) == source_sha256_prefixed(lf_path)


def test_keccak256_uses_evm_bytes32_format() -> None:
    assert (
        keccak256_hex(b"")
        == "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
