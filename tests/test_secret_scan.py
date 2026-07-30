from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCANNER = Path(__file__).parents[1] / "tools" / "scan_secrets.py"


def _scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _mnemonic_fixture() -> str:
    return 'mnemonic: "' + " ".join(
        (
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
        )
    ) + '"'


def test_secret_scan_rejects_private_key_fields_but_allows_hashes(
    tmp_path: Path,
) -> None:
    (tmp_path / "safe.json").write_text(
        '{"package_hash":"0x' + "ab" * 32 + '"}', encoding="utf-8"
    )
    assert _scan(tmp_path).returncode == 0

    (tmp_path / "unsafe.json").write_text(
        '{"private_key":"0x' + "12" * 32 + '"}', encoding="utf-8"
    )
    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "secret-valued field: unsafe.json:1" in result.stderr


def test_secret_scan_rejects_secret_file_names(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("VALUE=fixture", encoding="utf-8")
    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "forbidden secret file: .env.local" in result.stderr


def test_secret_scan_skips_only_top_level_transient_output_roots(
    tmp_path: Path,
) -> None:
    mnemonic = _mnemonic_fixture()
    for root_name in (".tmp", "tmp", "temp"):
        root = tmp_path / root_name
        root.mkdir()
        (root / "run.txt").write_text(mnemonic, encoding="utf-8")

    assert _scan(tmp_path).returncode == 0

    nested = tmp_path / "src" / "tmp"
    nested.mkdir(parents=True)
    (nested / "source.txt").write_text(mnemonic, encoding="utf-8")
    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "secret-valued field: src/tmp/source.txt:1" in result.stderr


def test_secret_scan_does_not_skip_a_top_level_file_named_tmp(tmp_path: Path) -> None:
    (tmp_path / "tmp").write_text(
        _mnemonic_fixture(),
        encoding="utf-8",
    )

    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "secret-valued field: tmp:1" in result.stderr


def test_secret_scan_does_not_respect_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "secret.txt").write_text(
        _mnemonic_fixture(),
        encoding="utf-8",
    )

    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "secret-valued field: ignored/secret.txt:1" in result.stderr
