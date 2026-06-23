from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.package import (
    build_package,
    install_package,
    package_self_check,
    verify_package,
)


ROOT = Path(__file__).resolve().parents[1]


def test_skill_entry_is_codex_discoverable_and_thin() -> None:
    skill_path = ROOT / "skills" / "loveengine-witness" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    _, raw_frontmatter, body = text.split("---", 2)
    frontmatter = {}
    for line in raw_frontmatter.strip().splitlines():
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()

    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "loveengine-witness"
    assert "LoveEngine" in frontmatter["description"]
    assert "manifest verify" in body
    assert "private key" in body.lower()
    assert len(body.splitlines()) < 120
    assert (skill_path.parent / "agents" / "openai.yaml").is_file()


def test_deterministic_package_build_verify_install_and_self_check(
    tmp_path: Path,
) -> None:
    first = build_package(ROOT, tmp_path / "first")
    second = build_package(ROOT, tmp_path / "second")

    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert first.sha256 == second.sha256
    assert first.keccak256 == second.keccak256

    verified = verify_package(first.archive)
    assert verified["valid"] is True
    assert verified["version"] == "0.5.0-lan-pilot"
    assert verified["file_count"] > 20

    target = tmp_path / "installed"
    installed = install_package(first.archive, target)
    assert installed["installed"] is True
    assert package_self_check(target)["valid"] is True
    assert (target / "skills" / "loveengine-witness" / "SKILL.md").is_file()
    assert (target / "LICENSE").is_file()
    assert (target / "checksums.json").is_file()
    assert (target / "sbom.spdx.json").is_file()
    sbom = json.loads((target / "sbom.spdx.json").read_text(encoding="utf-8"))
    assert sbom["packages"][0]["licenseDeclared"] == "LicenseRef-SCC0"

    with pytest.raises(LoveEngineError) as error:
        install_package(first.archive, target)
    assert error.value.code == "package_target_not_empty"


def test_package_verify_rejects_tampering_and_unsafe_paths(tmp_path: Path) -> None:
    result = build_package(ROOT, tmp_path / "release")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(result.archive) as source, zipfile.ZipFile(
        tampered, "w"
    ) as output:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "pyproject.toml":
                data += b"\n# tampered\n"
            output.writestr(info, data)

    with pytest.raises(LoveEngineError) as error:
        verify_package(tampered)
    assert error.value.code == "package_checksum_mismatch"

    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as output:
        output.writestr("../escape.txt", "unsafe")
        output.writestr(
            "checksums.json",
            json.dumps({"schema_version": "loveengine.package-checksums/1", "files": {}}),
        )
    with pytest.raises(LoveEngineError) as error:
        install_package(unsafe, tmp_path / "unsafe-target")
    assert error.value.code == "unsafe_archive_path"

    secret = tmp_path / "secret.zip"
    with zipfile.ZipFile(result.archive) as source, zipfile.ZipFile(
        secret, "w"
    ) as output:
        checksums = json.loads(source.read("checksums.json"))
        checksums["files"][".env"] = (
            "sha256:"
            + __import__("hashlib").sha256(b"TOKEN=unsafe").hexdigest()
        )
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "checksums.json":
                data = json.dumps(checksums).encode()
            output.writestr(info, data)
        output.writestr(".env", b"TOKEN=unsafe")
    with pytest.raises(LoveEngineError) as error:
        verify_package(secret)
    assert error.value.code == "package_secret_file_forbidden"


def test_package_excludes_secrets_and_repository_history(tmp_path: Path) -> None:
    result = build_package(ROOT, tmp_path / "release")
    with zipfile.ZipFile(result.archive) as archive:
        names = set(archive.namelist())
        joined = "\n".join(names).lower()
        assert ".venv" not in joined
        assert ".git" not in joined
        assert "docs/archive" not in joined
        assert "tests/" not in joined
        assert not any(name.endswith(".sqlite") for name in names)
        assert not any("private_key" in name.lower() for name in names)
