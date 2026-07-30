"""Deterministic, installable LoveEngine Skill release archives."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import TEXT_SOURCE_SUFFIXES, keccak256_hex, sha256_prefixed
from .release_identity import PROTOCOL_VERSION, SKILL_VERSION
from .schema import validate_schema
from .toolchain import verify_prepared_contract_artifacts


# Kept as a public compatibility alias for existing adapters.
PACKAGE_VERSION = SKILL_VERSION
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_FILE_SIZE = 128 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
KECCAK_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
CONTRACTS = (
    "WitnessDAO",
    "CorporateSink",
    "StreamingEngine",
    "PublicSink",
    "SkillRegistry",
)
REQUIRED_ARCHIVE_PATHS = {
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    "release.json",
    "checksums.json",
    "sbom.spdx.json",
    "skills/loveengine-witness/SKILL.md",
    "skills/loveengine-witness/agents/openai.yaml",
    "skills/loveengine-witness/skill-manifest.json",
    "plugins/loveengine-witness/.codex-plugin/plugin.json",
    "plugins/loveengine-witness/skills/loveengine-witness/SKILL.md",
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
TRANSIENT_ARCHIVE_ROOTS = {".tmp", "tmp", "temp"}
IGNORED_INSTALL_ROOTS = {".venv"}
IGNORED_INSTALL_CACHE_DIRS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


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
        or len(name) > 512
        or "\x00" in name
        or path.is_absolute()
        or "\\" in name
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part or len(part) > 255 for part in path.parts)
    ):
        raise LoveEngineError("unsafe_archive_path", name)
    lowered = [part.lower() for part in path.parts]
    if lowered[0] in TRANSIENT_ARCHIVE_ROOTS:
        raise LoveEngineError("unsafe_archive_path", name)
    if (
        any(part in FORBIDDEN_ARCHIVE_NAMES for part in lowered)
        or any(part.startswith(".env.") for part in lowered)
        or any("keystore" in part for part in lowered)
        or PurePosixPath(lowered[-1]).suffix in FORBIDDEN_ARCHIVE_SUFFIXES
    ):
        raise LoveEngineError("package_secret_file_forbidden", name)
    return path


def _runtime_files(root: Path) -> Iterable[tuple[str, bytes]]:
    # Do not let the standalone packaging route bypass the same attested
    # source/dependency/artifact boundary used by active Pilot paths.
    verify_prepared_contract_artifacts(root / "contracts")
    manifest_path = root / "skills/loveengine-witness/skill-manifest.json"
    try:
        manifest = _load_json_bytes(
            manifest_path.read_bytes(),
            code="package_manifest_invalid",
            detail=str(manifest_path),
        )
    except FileNotFoundError as exc:
        raise LoveEngineError("package_manifest_invalid", str(manifest_path), 3) from exc
    source_refs = manifest.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise LoveEngineError("package_manifest_invalid", "source_refs must be non-empty")

    fixed = [
        "LICENSE",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "README.zh-CN.md",
        "QA.md",
        "contracts/foundry.toml",
        "contracts/remappings.txt",
        "contracts/dependency-lock.json",
        "skills/loveengine-witness/SKILL.md",
        "skills/loveengine-witness/agents/openai.yaml",
        "skills/loveengine-witness/skill-manifest.json",
        "skills/loveengine-witness/skill-manifest.m0.json",
        "skills/loveengine-witness/agent-onboarding.md",
        "plugins/loveengine-witness/.codex-plugin/plugin.json",
        "plugins/loveengine-witness/skills/loveengine-witness/SKILL.md",
        "plugins/marketplace.example.json",
        "docs/api/README.md",
        "docs/api/agent-skill-api.md",
        "docs/api/agent-network-api.md",
        "docs/api/live-evidence-api.md",
        "docs/api/extension-interfaces.md",
        "docs/api/loveengine-contract-api.md",
        "docs/api/lan-pilot-api.md",
        "docs/api/cli-reference.md",
        "docs/development/contract2-comparison-and-recommendations.md",
        "docs/development/participant-runbook.zh-CN.md",
        "docs/architecture/witness-core-and-data-flow.zh-CN.md",
        "docs/specs/love-engine-master-plan.md",
        "docs/specs/love-engine-pre-enterprise-remote-lab.md",
    ]
    seen: set[str] = set()
    for relative_value in [*fixed, *source_refs]:
        if not isinstance(relative_value, str):
            raise LoveEngineError("package_manifest_invalid", "source_refs must be paths")
        relative = _safe_archive_path(relative_value).as_posix()
        if relative in seen:
            continue
        seen.add(relative)
        path = root / relative
        if not path.is_file():
            raise LoveEngineError("package_input_missing", relative, 3)
        if path.is_symlink():
            raise LoveEngineError("package_symlink_forbidden", relative)
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
            relative = path.relative_to(root).as_posix()
            if relative not in seen:
                seen.add(relative)
                yield relative, path.read_bytes()

    for name in CONTRACTS:
        artifact_path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
        if not artifact_path.is_file():
            raise LoveEngineError(
                "contract_artifact_missing",
                f"{name}: run loveengine pilot contracts prepare before packaging",
                3,
            )
        if artifact_path.is_symlink():
            raise LoveEngineError(
                "package_symlink_forbidden", artifact_path.relative_to(root).as_posix()
            )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        normalized = {
            "contractName": name,
            "abi": artifact["abi"],
            "bytecode": artifact["bytecode"]["object"],
            "deployedBytecode": artifact["deployedBytecode"]["object"],
        }
        relative = f"contracts/artifacts/{name}.json"
        if relative not in seen:
            seen.add(relative)
            yield relative, _json_bytes(normalized)


def _spdx(files: dict[str, bytes]) -> dict[str, Any]:
    license_id = "LicenseRef-SCC0"
    packages = [
        {
            "SPDXID": "SPDXRef-Package-LoveEngine",
            "name": "loveengine-witness-skill",
            "versionInfo": PACKAGE_VERSION,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": True,
            "licenseConcluded": license_id,
            "licenseDeclared": license_id,
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
                "licenseConcluded": license_id,
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
        "hasExtractedLicensingInfos": [
            {
                "licenseId": license_id,
                "name": "Smart Creative Commons Zero (SCC0)",
                "extractedText": files["LICENSE"].decode("utf-8"),
            }
        ],
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
    # Manifest source hashes already define repository text in canonical LF
    # form. Archive bytes must use that same representation, otherwise a
    # Windows CRLF checkout and an LF checkout produce different release
    # hashes for identical source content.
    files = {
        name: _source_bytes(name, data)
        for name, data in _runtime_files(root)
    }
    manifest = json.loads(files["skills/loveengine-witness/skill-manifest.json"])
    if manifest.get("version") != SKILL_VERSION or manifest.get(
        "protocol"
    ) != PROTOCOL_VERSION:
        raise LoveEngineError(
            "package_release_identity_mismatch",
            f"expected {SKILL_VERSION} / {PROTOCOL_VERSION}",
        )
    manifest_info = _verify_packaged_manifest(
        manifest, names=set(files), read_file=files.__getitem__
    )
    manifest_hash = keccak256_hex(canonical_json_bytes(manifest))
    release = {
        "schema_version": "loveengine.package-release/1",
        "skill_id": manifest["skill_id"],
        "version": manifest["version"],
        "protocol": manifest["protocol"],
        "archive_format": "zip",
        "registry_package_hash": "keccak256",
        "manifest_hash": manifest_hash,
        "contract_artifacts": [
            f"contracts/artifacts/{name}.json" for name in CONTRACTS
        ],
    }
    _verify_release_metadata(release, manifest, manifest_info, set(files))
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


def _load_json_bytes(data: bytes, *, code: str, detail: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=reject_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LoveEngineError(code, detail) from exc
    if not isinstance(value, dict):
        raise LoveEngineError(code, detail)
    return value


def _validate_checksum_map(
    checksums: dict[str, Any], archive_names: set[str], *, detail: str
) -> dict[str, str]:
    if checksums.get("schema_version") != "loveengine.package-checksums/1":
        raise LoveEngineError("package_checksums_invalid", detail)
    expected_files = checksums.get("files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise LoveEngineError("package_checksums_invalid", detail)
    if "checksums.json" in expected_files:
        raise LoveEngineError("package_checksums_invalid", "checksums.json is self-referential")
    validated: dict[str, str] = {}
    for raw_name, expected in expected_files.items():
        if not isinstance(raw_name, str) or not isinstance(expected, str):
            raise LoveEngineError("package_checksums_invalid", detail)
        name = _safe_archive_path(raw_name).as_posix()
        if name != raw_name or not SHA256_PATTERN.fullmatch(expected):
            raise LoveEngineError("package_checksums_invalid", raw_name)
        validated[name] = expected
    expected_names = archive_names - {"checksums.json"}
    missing = set(validated) - archive_names
    if missing:
        raise LoveEngineError("package_required_file_missing", sorted(missing)[0])
    unchecked = expected_names - set(validated)
    if unchecked:
        raise LoveEngineError("package_unchecked_file", sorted(unchecked)[0])
    unexpected = set(validated) - expected_names
    if unexpected:
        raise LoveEngineError("package_checksums_invalid", sorted(unexpected)[0])
    return validated


def _source_bytes(name: str, data: bytes) -> bytes:
    if PurePosixPath(name).suffix.lower() in TEXT_SOURCE_SUFFIXES:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _verify_packaged_manifest(
    manifest: dict[str, Any],
    *,
    names: set[str],
    read_file: Any,
) -> dict[str, Any]:
    schema_version = manifest.get("schema_version")
    if schema_version == "loveengine.skill-manifest/0.2":
        validate_schema(manifest, "skill-manifest-v2.schema.json")
    elif schema_version == "loveengine.skill-manifest/0.3":
        validate_schema(manifest, "skill-manifest-v3.schema.json")
    else:
        raise LoveEngineError("unsupported_schema_version", str(schema_version))

    refs = manifest.get("source_refs")
    hashes = manifest.get("source_hashes")
    if not isinstance(refs, list) or not refs or not isinstance(hashes, dict):
        raise LoveEngineError("package_manifest_invalid", "source refs/hashes required")
    if any(not isinstance(ref, str) for ref in refs):
        raise LoveEngineError("package_manifest_invalid", "source ref must be a path")
    if len(refs) != len(set(refs)):
        raise LoveEngineError("package_manifest_invalid", "duplicate source ref")
    if set(hashes) != set(refs):
        raise LoveEngineError("package_manifest_invalid", "source hash coverage mismatch")
    for raw_ref in refs:
        if not isinstance(raw_ref, str):
            raise LoveEngineError("package_manifest_invalid", "source ref must be a path")
        ref = _safe_archive_path(raw_ref).as_posix()
        if ref not in names:
            raise LoveEngineError("missing_source_ref", ref, 3)
        expected = hashes.get(ref)
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            raise LoveEngineError("package_manifest_invalid", f"invalid source hash: {ref}")
        actual = sha256_prefixed(_source_bytes(ref, read_file(ref)))
        if actual != expected:
            raise LoveEngineError("source_hash_mismatch", ref)

    package_view = dict(manifest)
    package_view["package_hash"] = "sha256:SELF"
    actual_package_hash = sha256_prefixed(canonical_json_bytes(package_view))
    if manifest.get("package_hash") != actual_package_hash:
        raise LoveEngineError("manifest_package_hash_mismatch", actual_package_hash)
    spec_ref = manifest.get("spec_ref")
    if spec_ref and manifest.get("spec_hash") != hashes.get(spec_ref):
        raise LoveEngineError("spec_hash_mismatch", str(spec_ref))
    return {
        "skill_id": manifest.get("skill_id"),
        "version": manifest.get("version"),
        "protocol": manifest.get("protocol"),
        "manifest_package_hash": actual_package_hash,
        "manifest_keccak256": keccak256_hex(canonical_json_bytes(manifest)),
        "source_count": len(refs),
    }


def _verify_release_metadata(
    release: dict[str, Any],
    manifest: dict[str, Any],
    manifest_info: dict[str, Any],
    names: set[str],
) -> None:
    required = {
        "schema_version",
        "skill_id",
        "version",
        "protocol",
        "archive_format",
        "registry_package_hash",
        "manifest_hash",
        "contract_artifacts",
    }
    if set(release) != required:
        raise LoveEngineError("package_release_invalid", "release metadata inventory mismatch")
    if release.get("schema_version") != "loveengine.package-release/1":
        raise LoveEngineError("package_release_invalid", "unsupported release schema")
    for field in ("skill_id", "version", "protocol"):
        if release.get(field) != manifest.get(field):
            raise LoveEngineError("package_release_manifest_mismatch", field)
    if release.get("archive_format") != "zip" or release.get("registry_package_hash") != "keccak256":
        raise LoveEngineError("package_release_invalid", "unsupported archive/hash metadata")
    if release.get("manifest_hash") != manifest_info["manifest_keccak256"]:
        raise LoveEngineError("package_release_manifest_mismatch", "manifest_hash")
    artifacts = release.get("contract_artifacts")
    expected_artifacts = {f"contracts/artifacts/{name}.json" for name in CONTRACTS}
    if (
        not isinstance(artifacts, list)
        or len(artifacts) != len(expected_artifacts)
        or set(artifacts) != expected_artifacts
    ):
        raise LoveEngineError("package_release_invalid", "contract artifact inventory mismatch")
    if not expected_artifacts.issubset(names):
        raise LoveEngineError("package_required_file_missing", sorted(expected_artifacts - names)[0])


def _canonical_zip_bytes(names: set[str], read_file: Any) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as target:
        for name in sorted(names):
            target.writestr(_zip_info(name), read_file(name))
    return output.getvalue()


def _read_verified_archive(
    path: Path,
) -> tuple[zipfile.ZipFile, dict[str, str], dict[str, Any]]:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_ARCHIVE_SIZE:
            raise LoveEngineError("package_archive_too_large", str(path))
    except FileNotFoundError as exc:
        raise LoveEngineError("invalid_package_archive", str(path), 3) from exc
    try:
        archive = zipfile.ZipFile(path)
    except (FileNotFoundError, IsADirectoryError, PermissionError, zipfile.BadZipFile) as exc:
        raise LoveEngineError("invalid_package_archive", str(path), 3) from exc
    names: set[str] = set()
    total_size = 0
    for info in archive.infolist():
        try:
            _safe_archive_path(info.filename)
        except LoveEngineError:
            archive.close()
            raise
        if info.is_dir():
            archive.close()
            raise LoveEngineError("package_directory_entry_forbidden", info.filename)
        if info.filename in names:
            archive.close()
            raise LoveEngineError("duplicate_archive_path", info.filename)
        names.add(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            archive.close()
            raise LoveEngineError("package_symlink_forbidden", info.filename)
        if info.flag_bits & 0x1:
            archive.close()
            raise LoveEngineError("package_encryption_forbidden", info.filename)
        if info.file_size > MAX_ARCHIVE_FILE_SIZE:
            archive.close()
            raise LoveEngineError("package_file_too_large", info.filename)
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_SIZE or len(names) > MAX_ARCHIVE_FILES:
            archive.close()
            raise LoveEngineError("package_archive_too_large", str(path))
    missing = REQUIRED_ARCHIVE_PATHS - names
    if missing:
        archive.close()
        raise LoveEngineError("package_required_file_missing", sorted(missing)[0])
    try:
        checksums_value = _load_json_bytes(
            archive.read("checksums.json"),
            code="package_checksums_invalid",
            detail=str(path),
        )
    except KeyError as exc:
        archive.close()
        raise LoveEngineError("package_checksums_invalid", str(path)) from exc
    try:
        checksums = _validate_checksum_map(checksums_value, names, detail=str(path))
        for name, expected in checksums.items():
            data = archive.read(name)
            if len(data) > MAX_ARCHIVE_FILE_SIZE or sha256_prefixed(data) != expected:
                raise LoveEngineError("package_checksum_mismatch", name)
        manifest = _load_json_bytes(
            archive.read("skills/loveengine-witness/skill-manifest.json"),
            code="package_manifest_invalid",
            detail=str(path),
        )
        manifest_info = _verify_packaged_manifest(
            manifest, names=names, read_file=archive.read
        )
        release = _load_json_bytes(
            archive.read("release.json"), code="package_release_invalid", detail=str(path)
        )
        _verify_release_metadata(release, manifest, manifest_info, names)
        actual_bytes = path.read_bytes()
        if _canonical_zip_bytes(names, archive.read) != actual_bytes:
            raise LoveEngineError("package_archive_not_deterministic", str(path))
    except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        archive.close()
        raise LoveEngineError("invalid_package_archive", str(path), 3) from exc
    except LoveEngineError:
        archive.close()
        raise
    metadata = {
        "release": release,
        **manifest_info,
        "archive_sha256": sha256_prefixed(actual_bytes),
        "archive_keccak256": keccak256_hex(actual_bytes),
    }
    return archive, checksums, metadata


def _verify_trust(
    actual_package_hash: str,
    *,
    expected_package_hash: str | None,
    integrity_only: bool,
) -> bool:
    if expected_package_hash is None:
        if not integrity_only:
            raise LoveEngineError(
                "package_trust_required",
                "provide expected_package_hash or explicitly select integrity_only",
            )
        return False
    if not isinstance(expected_package_hash, str) or not KECCAK_PATTERN.fullmatch(
        expected_package_hash
    ):
        raise LoveEngineError("invalid_expected_package_hash", str(expected_package_hash))
    if actual_package_hash.lower() != expected_package_hash.lower():
        raise LoveEngineError("package_hash_mismatch", actual_package_hash)
    return True


def _validate_trust_request(
    *, expected_package_hash: str | None, integrity_only: bool
) -> None:
    if expected_package_hash is None and not integrity_only:
        raise LoveEngineError(
            "package_trust_required",
            "provide expected_package_hash or explicitly select integrity_only",
        )
    if expected_package_hash is not None and integrity_only:
        raise LoveEngineError(
            "package_trust_mode_conflict",
            "expected_package_hash and integrity_only are mutually exclusive",
        )
    if expected_package_hash is not None and (
        not isinstance(expected_package_hash, str)
        or not KECCAK_PATTERN.fullmatch(expected_package_hash)
    ):
        raise LoveEngineError("invalid_expected_package_hash", str(expected_package_hash))


def verify_package(
    path: Path,
    *,
    expected_package_hash: str | None = None,
    integrity_only: bool = False,
) -> dict[str, Any]:
    _validate_trust_request(
        expected_package_hash=expected_package_hash, integrity_only=integrity_only
    )
    archive, checksums, metadata = _read_verified_archive(Path(path))
    try:
        trust_bound = _verify_trust(
            metadata["archive_keccak256"],
            expected_package_hash=expected_package_hash,
            integrity_only=integrity_only,
        )
        release = metadata["release"]
        return {
            "valid": True,
            "trust_bound": trust_bound,
            "verification_level": "registry_hash_bound" if trust_bound else "integrity_only",
            "skill_id": release["skill_id"],
            "version": release["version"],
            "protocol": release["protocol"],
            "file_count": len(archive.infolist()),
            "archive_sha256": metadata["archive_sha256"],
            "archive_keccak256": metadata["archive_keccak256"],
            "manifest_keccak256": metadata["manifest_keccak256"],
            "manifest_package_hash": metadata["manifest_package_hash"],
            "checked_file_count": len(checksums),
            "source_count": metadata["source_count"],
        }
    finally:
        archive.close()


def _remove_install_staging(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def install_package(
    path: Path,
    target: Path,
    *,
    expected_package_hash: str | None = None,
    integrity_only: bool = False,
) -> dict[str, Any]:
    _validate_trust_request(
        expected_package_hash=expected_package_hash, integrity_only=integrity_only
    )
    archive, _, metadata = _read_verified_archive(Path(path))
    staging: Path | None = None
    try:
        raw_target = Path(target)
        if raw_target.is_symlink():
            raise LoveEngineError("package_symlink_forbidden", str(raw_target))
        target = raw_target.resolve()
        if target == target.parent:
            raise LoveEngineError("unsafe_package_target", str(target))
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise LoveEngineError("package_target_not_empty", str(target))
        trust_bound = _verify_trust(
            metadata["archive_keccak256"],
            expected_package_hash=expected_package_hash,
            integrity_only=integrity_only,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.install-", dir=target.parent)
        ).resolve()
        for info in archive.infolist():
            relative = _safe_archive_path(info.filename)
            destination = (staging / Path(*relative.parts)).resolve()
            if staging not in destination.parents:
                raise LoveEngineError("unsafe_archive_path", info.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
        check = package_self_check(
            staging,
            expected_package_hash=(metadata["archive_keccak256"] if trust_bound else None),
            integrity_only=not trust_bound,
        )
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        staging = None
    except Exception:
        if staging is not None:
            _remove_install_staging(staging)
        raise
    finally:
        archive.close()
    return {"installed": True, "target": str(target), **check}


def package_self_check(
    root: Path,
    *,
    expected_package_hash: str | None = None,
    integrity_only: bool = False,
) -> dict[str, Any]:
    _validate_trust_request(
        expected_package_hash=expected_package_hash, integrity_only=integrity_only
    )
    raw_root = Path(root)
    if raw_root.is_symlink():
        raise LoveEngineError("package_symlink_forbidden", str(raw_root))
    root = raw_root.resolve()
    checksums_path = root / "checksums.json"
    try:
        checksums_value = _load_json_bytes(
            checksums_path.read_bytes(),
            code="package_checksums_invalid",
            detail=str(root),
        )
    except FileNotFoundError as exc:
        raise LoveEngineError("package_checksums_invalid", str(root)) from exc
    names: set[str] = set()
    total_size = 0
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        kept_directories: list[str] = []
        for directory_name in directory_names:
            directory = current_path / directory_name
            relative = relative_current / directory_name
            if directory.is_symlink():
                raise LoveEngineError(
                    "package_symlink_forbidden", relative.as_posix()
                )
            if (
                (
                    relative_current == Path(".")
                    and directory_name in IGNORED_INSTALL_ROOTS
                )
                or directory_name in IGNORED_INSTALL_CACHE_DIRS
            ):
                continue
            kept_directories.append(directory_name)
        directory_names[:] = kept_directories

        for file_name in file_names:
            entry = current_path / file_name
            relative = relative_current / file_name
            if entry.is_symlink():
                raise LoveEngineError(
                    "package_symlink_forbidden", relative.as_posix()
                )
            name = relative.as_posix()
            _safe_archive_path(name)
            size = entry.stat().st_size
            if size > MAX_ARCHIVE_FILE_SIZE:
                raise LoveEngineError("package_file_too_large", name)
            names.add(name)
            total_size += size
            if len(names) > MAX_ARCHIVE_FILES or total_size > MAX_ARCHIVE_UNCOMPRESSED_SIZE:
                raise LoveEngineError("package_archive_too_large", str(root))
    required_missing = REQUIRED_ARCHIVE_PATHS - names
    if required_missing:
        raise LoveEngineError("package_required_file_missing", sorted(required_missing)[0])
    checksums = _validate_checksum_map(checksums_value, names, detail=str(root))

    def read_file(name: str) -> bytes:
        return (root / Path(*PurePosixPath(name).parts)).read_bytes()

    for name, expected in checksums.items():
        if sha256_prefixed(read_file(name)) != expected:
            raise LoveEngineError("package_checksum_mismatch", name)
    manifest = _load_json_bytes(
        read_file("skills/loveengine-witness/skill-manifest.json"),
        code="package_manifest_invalid",
        detail=str(root),
    )
    manifest_info = _verify_packaged_manifest(
        manifest, names=names, read_file=read_file
    )
    release = _load_json_bytes(
        read_file("release.json"), code="package_release_invalid", detail=str(root)
    )
    _verify_release_metadata(release, manifest, manifest_info, names)
    canonical_archive_hash = keccak256_hex(_canonical_zip_bytes(names, read_file))
    trust_bound = _verify_trust(
        canonical_archive_hash,
        expected_package_hash=expected_package_hash,
        integrity_only=integrity_only,
    )
    return {
        "valid": True,
        "trust_bound": trust_bound,
        "verification_level": "registry_hash_bound" if trust_bound else "integrity_only",
        "skill_id": release["skill_id"],
        "version": release["version"],
        "protocol": release["protocol"],
        "root": str(root),
        "archive_keccak256": canonical_archive_hash,
        "manifest_keccak256": manifest_info["manifest_keccak256"],
        "checked_file_count": len(checksums),
        "source_count": manifest_info["source_count"],
    }
