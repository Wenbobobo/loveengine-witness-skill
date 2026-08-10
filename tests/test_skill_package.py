from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
import loveengine_witness.package as package_module
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import sha256_prefixed
from loveengine_witness.package import (
    build_package,
    install_package,
    package_self_check,
    verify_package,
)


ROOT = Path(__file__).resolve().parents[1]


def _rewrite_archive(
    source_path: Path,
    target_path: Path,
    mutate: Callable[[dict[str, bytes]], None],
) -> None:
    with zipfile.ZipFile(source_path) as source:
        entries = {info.filename: source.read(info.filename) for info in source.infolist()}
        infos = {info.filename: info for info in source.infolist()}
    mutate(entries)
    with zipfile.ZipFile(target_path, "w") as target:
        for name in sorted(entries):
            target.writestr(infos[name], entries[name])


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


@pytest.mark.parametrize(
    "name",
    ("tmp/payload.txt", ".tmp/payload.txt", "temp/payload.txt", "TMP/payload.txt"),
)
def test_package_rejects_top_level_transient_output_paths(name: str) -> None:
    with pytest.raises(LoveEngineError) as error:
        package_module._safe_archive_path(name)
    assert error.value.code == "unsafe_archive_path"
    assert package_module._safe_archive_path("src/tmp/payload.txt").as_posix() == (
        "src/tmp/payload.txt"
    )


@pytest.mark.integration
def test_deterministic_package_build_verify_install_and_self_check(
    tmp_path: Path,
) -> None:
    first = build_package(ROOT, tmp_path / "first")
    second = build_package(ROOT, tmp_path / "second")

    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert first.sha256 == second.sha256
    assert first.keccak256 == second.keccak256

    with pytest.raises(LoveEngineError) as error:
        verify_package(first.archive)
    assert error.value.code == "package_trust_required"

    integrity = verify_package(first.archive, integrity_only=True)
    assert integrity["trust_bound"] is False
    verified = verify_package(
        first.archive, expected_package_hash=first.keccak256
    )
    assert verified["valid"] is True
    assert verified["trust_bound"] is True
    assert verified["version"] == "0.6.1-contract-public-pilot"
    assert verified["file_count"] > 20

    target = tmp_path / "installed"
    installed = install_package(
        first.archive, target, expected_package_hash=first.keccak256
    )
    assert installed["installed"] is True
    assert installed["trust_bound"] is True
    assert package_self_check(
        target, expected_package_hash=first.keccak256
    )["valid"] is True
    dependency_certificate = (
        target / ".venv" / "lib" / "site-packages" / "certifi" / "cacert.pem"
    )
    dependency_certificate.parent.mkdir(parents=True)
    dependency_certificate.write_text("dependency trust store", encoding="utf-8")
    bytecode_cache = target / "src" / "loveengine_witness" / "__pycache__"
    bytecode_cache.mkdir()
    (bytecode_cache / "package.cpython-312.pyc").write_bytes(b"generated")
    assert package_self_check(
        target, expected_package_hash=first.keccak256
    )["valid"] is True

    (target / ".env").write_text("TOKEN=unsafe", encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        package_self_check(target, expected_package_hash=first.keccak256)
    assert error.value.code == "package_secret_file_forbidden"
    (target / ".env").unlink()

    assert (target / "skills" / "loveengine-witness" / "SKILL.md").is_file()
    assert (target / "LICENSE").is_file()
    assert (target / "checksums.json").is_file()
    assert (target / "sbom.spdx.json").is_file()
    sbom = json.loads((target / "sbom.spdx.json").read_text(encoding="utf-8"))
    assert sbom["packages"][0]["licenseDeclared"] == "LicenseRef-SCC0"

    with pytest.raises(LoveEngineError) as error:
        install_package(first.archive, target, integrity_only=True)
    assert error.value.code == "package_target_not_empty"

    with pytest.raises(LoveEngineError) as error:
        verify_package(first.archive, expected_package_hash="0x" + "0" * 64)
    assert error.value.code == "package_hash_mismatch"


@pytest.mark.integration
def test_package_build_normalizes_text_line_endings_before_archiving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = build_package(ROOT, tmp_path / "first")
    original_runtime_files = package_module._runtime_files

    def runtime_files_with_crlf(root: Path):
        for name, data in original_runtime_files(root):
            if name == "README.md":
                canonical = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                yield name, canonical.replace(b"\n", b"\r\n")
            else:
                yield name, data

    monkeypatch.setattr(package_module, "_runtime_files", runtime_files_with_crlf)
    second = build_package(ROOT, tmp_path / "second")

    assert first.archive.read_bytes() == second.archive.read_bytes()
    with zipfile.ZipFile(second.archive) as archive:
        assert b"\r" not in archive.read("README.md")


@pytest.mark.integration
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
        verify_package(tampered, integrity_only=True)
    assert error.value.code == "package_checksum_mismatch"

    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as output:
        output.writestr("../escape.txt", "unsafe")
        output.writestr(
            "checksums.json",
            json.dumps({"schema_version": "loveengine.package-checksums/1", "files": {}}),
        )
    with pytest.raises(LoveEngineError) as error:
        install_package(unsafe, tmp_path / "unsafe-target", integrity_only=True)
    assert error.value.code == "unsafe_archive_path"

    for secret_name in (".env", ".env.production"):
        secret = tmp_path / f"{secret_name.removeprefix('.')}.zip"
        with zipfile.ZipFile(result.archive) as source, zipfile.ZipFile(
            secret, "w"
        ) as output:
            checksums = json.loads(source.read("checksums.json"))
            checksums["files"][secret_name] = (
                "sha256:"
                + __import__("hashlib").sha256(b"TOKEN=unsafe").hexdigest()
            )
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "checksums.json":
                    data = json.dumps(checksums).encode()
                output.writestr(info, data)
            output.writestr(secret_name, b"TOKEN=unsafe")
        with pytest.raises(LoveEngineError) as error:
            verify_package(secret, integrity_only=True)
        assert error.value.code == "package_secret_file_forbidden"


@pytest.mark.integration
def test_package_excludes_secrets_and_repository_history(tmp_path: Path) -> None:
    result = build_package(ROOT, tmp_path / "release")
    with zipfile.ZipFile(result.archive) as archive:
        names = set(archive.namelist())
        joined = "\n".join(names).lower()
        assert ".venv" not in joined
        assert ".git" not in joined
        assert "tests/" not in joined
        assert not any(name.endswith(".sqlite") for name in names)
        assert not any("private_key" in name.lower() for name in names)
        manifest = json.loads(
            archive.read("skills/loveengine-witness/skill-manifest.json")
        )
        assert set(manifest["source_refs"]).issubset(names)


@pytest.mark.integration
def test_package_rejects_empty_checksums_and_rewritten_source_hashes(
    tmp_path: Path,
) -> None:
    result = build_package(ROOT, tmp_path / "release")

    empty = tmp_path / "empty-checksums.zip"

    def empty_checksums(entries: dict[str, bytes]) -> None:
        value = json.loads(entries["checksums.json"])
        value["files"] = {}
        entries["checksums.json"] = json.dumps(value).encode("utf-8")

    _rewrite_archive(result.archive, empty, empty_checksums)
    with pytest.raises(LoveEngineError) as error:
        verify_package(empty, integrity_only=True)
    assert error.value.code == "package_checksums_invalid"

    rewritten = tmp_path / "rewritten-source.zip"

    def rewrite_source(entries: dict[str, bytes]) -> None:
        name = "docs/specs/love-engine-master-plan.md"
        entries[name] += b"\nrewritten\n"
        checksums = json.loads(entries["checksums.json"])
        checksums["files"][name] = sha256_prefixed(entries[name])
        entries["checksums.json"] = json.dumps(checksums).encode("utf-8")

    _rewrite_archive(result.archive, rewritten, rewrite_source)
    with pytest.raises(LoveEngineError) as error:
        verify_package(rewritten, integrity_only=True)
    assert error.value.code == "source_hash_mismatch"


@pytest.mark.integration
def test_package_install_cleans_staging_on_failed_self_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_package(ROOT, tmp_path / "release")
    target = tmp_path / "atomic-install"

    def fail_self_check(*args: object, **kwargs: object) -> dict[str, object]:
        raise LoveEngineError("forced_self_check_failure", "fixture")

    monkeypatch.setattr(package_module, "package_self_check", fail_self_check)
    with pytest.raises(LoveEngineError) as error:
        install_package(result.archive, target, integrity_only=True)
    assert error.value.code == "forced_self_check_failure"
    assert not target.exists()
    assert list(tmp_path.glob(".atomic-install.install-*")) == []
