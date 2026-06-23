"""Deterministic, installable LoveEngine Skill release archives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed


PACKAGE_VERSION = "0.5.0-lan-pilot"
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
CONTRACTS = (
    "WitnessDAO",
    "CorporateSink",
    "StreamingEngine",
    "PublicSink",
    "SkillRegistry",
)
REQUIRED_ARCHIVE_PATHS = {
    "pyproject.toml",
    "uv.lock",
    "release.json",
    "checksums.json",
    "sbom.spdx.json",
    "skills/loveengine-witness/SKILL.md",
    "skills/loveengine-witness/agents/openai.yaml",
    "skills/loveengine-witness/skill-manifest.json",
}
FORBIDDEN_ARCHIVE_NAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_ed25519",
    "mnemonic",
    "keystore",
}
FORBIDDEN_ARCHIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


@dataclass(frozen=True)
class PackageBuildResult:
    archive: Path
    sha256: str
    keccak256: str
    file_count: int
    sbom: Path
    checksums: Path


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"


def _safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise LoveEngineError("unsafe_archive_path", name)
    lowered = [part.lower() for part in path.parts]
    if (
        any(part in FORBIDDEN_ARCHIVE_NAMES for part in lowered)
        or any("keystore" in part for part in lowered)
        or PurePosixPath(lowered[-1]).suffix in FORBIDDEN_ARCHIVE_SUFFIXES
    ):
        raise LoveEngineError("package_secret_file_forbidden", name)
    return path


def _runtime_files(root: Path) -> Iterable[tuple[str, bytes]]:
    fixed = (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "contracts/foundry.toml",
        "contracts/remappings.txt",
        "skills/loveengine-witness/SKILL.md",
        "skills/loveengine-witness/agents/openai.yaml",
        "skills/loveengine-witness/skill-manifest.json",
        "skills/loveengine-witness/skill-manifest.m0.json",
        "skills/loveengine-witness/agent-onboarding.md",
        "docs/api/README.md",
        "docs/api/agent-skill-api.md",
        "docs/api/agent-network-api.md",
        "docs/api/live-evidence-api.md",
        "docs/api/extension-interfaces.md",
        "docs/specs/love-engine-master-plan.md",
        "docs/specs/love-engine-lan-pilot-spec.md",
    )
    for relative in fixed:
        path = root / relative
        if not path.is_file():
            raise LoveEngineError("package_input_missing", relative, 3)
        yield relative, path.read_bytes()

    for directory, pattern in (
        ("src/loveengine_witness", "*.py"),
        ("schemas", "*.json"),
        ("contracts/src", "*.sol"),
    ):
        for path in sorted((root / directory).rglob(pattern)):
            if path.is_symlink():
                raise LoveEngineError(
                    "package_symlink_forbidden", path.relative_to(root).as_posix()
                )
            yield path.relative_to(root).as_posix(), path.read_bytes()

    for name in CONTRACTS:
        artifact_path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
        if not artifact_path.is_file():
            raise LoveEngineError(
                "contract_artifact_missing",
                f"{name}: run pinned Foundry build before packaging",
                3,
            )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        normalized = {
            "contractName": name,
            "abi": artifact["abi"],
            "bytecode": artifact["bytecode"]["object"],
            "deployedBytecode": artifact["deployedBytecode"]["object"],
        }
        yield f"contracts/artifacts/{name}.json", _json_bytes(normalized)


def _spdx(files: dict[str, bytes]) -> dict[str, Any]:
    packages = [
        {
            "SPDXID": "SPDXRef-Package-LoveEngine",
            "name": "loveengine-witness-skill",
            "versionInfo": PACKAGE_VERSION,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": True,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
    ]
    file_entries = []
    relationships = []
    for index, (name, data) in enumerate(sorted(files.items()), start=1):
        file_id = f"SPDXRef-File-{index}"
        file_entries.append(
            {
                "SPDXID": file_id,
                "fileName": f"./{name}",
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": hashlib.sha256(data).hexdigest(),
                    }
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-LoveEngine",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"loveengine-witness-{PACKAGE_VERSION}",
        "documentNamespace": (
            "https://loveengine.local/spdx/"
            + hashlib.sha256(canonical_json_bytes(sorted(files))).hexdigest()
        ),
        "creationInfo": {
            "created": "1980-01-01T00:00:00Z",
            "creators": ["Tool: loveengine-package-builder"],
        },
        "packages": packages,
        "files": file_entries,
        "relationships": relationships,
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ARCHIVE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_package(root: Path, output: Path) -> PackageBuildResult:
    root = Path(root).resolve()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    files = dict(_runtime_files(root))
    release = {
        "schema_version": "loveengine.package-release/1",
        "skill_id": "loveengine-witness",
        "version": PACKAGE_VERSION,
        "protocol": "loveengine-witness-net/0.5",
        "archive_format": "zip",
        "registry_package_hash": "keccak256",
        "contract_artifacts": [
            f"contracts/artifacts/{name}.json" for name in CONTRACTS
        ],
    }
    files["release.json"] = _json_bytes(release)
    files["sbom.spdx.json"] = _json_bytes(_spdx(files))
    checksums_value = {
        "schema_version": "loveengine.package-checksums/1",
        "files": {
            name: sha256_prefixed(data) for name, data in sorted(files.items())
        },
    }
    files["checksums.json"] = _json_bytes(checksums_value)

    archive = output / f"loveengine-witness-{PACKAGE_VERSION}.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as target:
        for name, data in sorted(files.items()):
            target.writestr(_zip_info(name), data)

    archive_bytes = archive.read_bytes()
    sha256 = sha256_prefixed(archive_bytes)
    keccak = keccak256_hex(archive_bytes)
    external_checksums = output / f"{archive.name}.checksums.json"
    external_checksums.write_bytes(
        _json_bytes(
            {
                "schema_version": "loveengine.release-checksums/1",
                "archive": archive.name,
                "sha256": sha256,
                "keccak256": keccak,
            }
        )
    )
    external_sbom = output / f"{archive.name}.sbom.spdx.json"
    external_sbom.write_bytes(files["sbom.spdx.json"])
    return PackageBuildResult(
        archive=archive,
        sha256=sha256,
        keccak256=keccak,
        file_count=len(files),
        sbom=external_sbom,
        checksums=external_checksums,
    )


def _read_verified_archive(path: Path) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(path)
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        raise LoveEngineError("invalid_package_archive", str(path), 3) from exc
    names: set[str] = set()
    for info in archive.infolist():
        _safe_archive_path(info.filename)
        if info.filename in names:
            archive.close()
            raise LoveEngineError("duplicate_archive_path", info.filename)
        names.add(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            archive.close()
            raise LoveEngineError("package_symlink_forbidden", info.filename)
    missing = REQUIRED_ARCHIVE_PATHS - names
    if missing:
        archive.close()
        raise LoveEngineError("package_required_file_missing", sorted(missing)[0])
    try:
        checksums = json.loads(archive.read("checksums.json"))
    except (KeyError, json.JSONDecodeError) as exc:
        archive.close()
        raise LoveEngineError("package_checksums_invalid", str(path)) from exc
    expected_files = checksums.get("files")
    if not isinstance(expected_files, dict):
        archive.close()
        raise LoveEngineError("package_checksums_invalid", str(path))
    for name, expected in expected_files.items():
        if name not in names:
            archive.close()
            raise LoveEngineError("package_required_file_missing", name)
        if sha256_prefixed(archive.read(name)) != expected:
            archive.close()
            raise LoveEngineError("package_checksum_mismatch", name)
    unchecked = names - set(expected_files) - {"checksums.json"}
    if unchecked:
        archive.close()
        raise LoveEngineError("package_unchecked_file", sorted(unchecked)[0])
    return archive, checksums


def verify_package(path: Path) -> dict[str, Any]:
    archive, checksums = _read_verified_archive(Path(path))
    try:
        release = json.loads(archive.read("release.json"))
        return {
            "valid": True,
            "skill_id": release["skill_id"],
            "version": release["version"],
            "protocol": release["protocol"],
            "file_count": len(archive.infolist()),
            "archive_sha256": sha256_prefixed(Path(path).read_bytes()),
            "archive_keccak256": keccak256_hex(Path(path).read_bytes()),
            "checked_file_count": len(checksums["files"]),
        }
    finally:
        archive.close()


def install_package(path: Path, target: Path) -> dict[str, Any]:
    archive, _ = _read_verified_archive(Path(path))
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        archive.close()
        raise LoveEngineError("package_target_not_empty", str(target))
    target.mkdir(parents=True, exist_ok=True)
    try:
        for info in archive.infolist():
            relative = _safe_archive_path(info.filename)
            destination = (target / Path(*relative.parts)).resolve()
            if target not in destination.parents and destination != target:
                raise LoveEngineError("unsafe_archive_path", info.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
    finally:
        archive.close()
    check = package_self_check(target)
    return {"installed": True, "target": str(target), **check}


def package_self_check(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    checksums_path = root / "checksums.json"
    try:
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise LoveEngineError("package_checksums_invalid", str(root)) from exc
    for name, expected in checksums.get("files", {}).items():
        path = root / Path(*PurePosixPath(name).parts)
        if not path.is_file():
            raise LoveEngineError("package_required_file_missing", name)
        if sha256_prefixed(path.read_bytes()) != expected:
            raise LoveEngineError("package_checksum_mismatch", name)
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    return {
        "valid": True,
        "skill_id": release["skill_id"],
        "version": release["version"],
        "root": str(root),
    }
