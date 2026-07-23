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
