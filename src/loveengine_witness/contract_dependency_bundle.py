"""Deterministic, offline transport for locked Foundry dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .toolchain import (
    CONTRACTS_ROOT,
    CONTRACT_DEPENDENCIES,
    _contracts_root,
    _dependency_inventories,
    _dependency_lock,
    _dependency_lock_attestation,
    _is_link_or_reparse_point,
    _is_sha256,
    _remove_owned_tree,
    _stable_file_bytes,
    _tree_inventory,
)


BUNDLE_SCHEMA_VERSION = "loveengine.contract-dependency-bundle/1"
BUNDLE_RESULT_SCHEMA_VERSION = "loveengine.contract-dependency-bundle-result/1"
MANIFEST_NAME = "loveengine-contract-dependencies.json"
MAX_BUNDLE_FILES = 50_000
MAX_BUNDLE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _bundle_error() -> LoveEngineError:
    return LoveEngineError(
        "contract_dependency_bundle_invalid",
        "contract dependency bundle is invalid",
        3,
    )


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _bundle_error() from exc
    return "sha256:" + digest.hexdigest()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _bundle_result(
    *,
    operation: str,
    archive: Path,
    archive_sha256: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": BUNDLE_RESULT_SCHEMA_VERSION,
        "operation": operation,
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "manifest_sha256": sha256_prefixed(canonical_json_bytes(manifest)),
        "file_count": len(manifest["files"]),
        "total_size": manifest["total_size"],
        "dependencies": manifest["dependencies"],
    }


def build_contract_dependency_bundle(
    archive: Path,
    *,
    contracts_root: Path = CONTRACTS_ROOT,
) -> dict[str, Any]:
    """Build one deterministic ZIP from dependency trees matching the lock."""

    root = _contracts_root(Path(contracts_root))
    lock = _dependency_lock(root)
    inventories = _dependency_inventories(root, dependency_lock=lock)
    records: list[dict[str, Any]] = []
    contents_by_path: dict[str, bytes] = {}
    for inventory in inventories:
        name = inventory["name"]
        dependency_root = root / "lib" / name
        for record in inventory["files"]:
            relative = record["path"]
            source = dependency_root.joinpath(*PurePosixPath(relative).parts)
            _, contents = _stable_file_bytes(
                source,
                error_code="contract_dependency_bundle_invalid",
                detail=name,
            )
            archive_path = f"contracts/lib/{name}/{relative}"
            bundled = {
                "path": archive_path,
                "size": len(contents),
                "sha256": sha256_prefixed(contents),
            }
            if bundled["size"] != record["size"] or bundled["sha256"] != record[
                "sha256"
            ]:
                raise _bundle_error()
            records.append(bundled)
            contents_by_path[archive_path] = contents
    records.sort(key=lambda item: item["path"])
    total_size = sum(int(item["size"]) for item in records)
    if (
        not records
        or len(records) > MAX_BUNDLE_FILES
        or total_size > MAX_BUNDLE_UNCOMPRESSED_BYTES
    ):
        raise _bundle_error()
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "dependency_lock": _dependency_lock_attestation(root),
        "dependencies": [
            {
                "name": item["name"],
                "package": item["package"],
                "commit": item["commit"],
                "tree_sha256": item["tree_sha256"],
                "file_count": item["file_count"],
            }
            for item in inventories
        ],
        "files": records,
        "total_size": total_size,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise _bundle_error()

    output = Path(os.path.abspath(os.fspath(archive)))
    if not output.parent.is_dir() or _is_link_or_reparse_point(output.parent):
        raise _bundle_error()
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".loveengine-contract-dependencies-",
            suffix=".zip",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(raw_temporary)
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as target:
            target.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
            for record in records:
                target.writestr(
                    _zip_info(record["path"]),
                    contents_by_path[record["path"]],
                )
        os.replace(temporary, output)
        temporary = None
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise _bundle_error() from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _bundle_result(
        operation="build",
        archive=output,
        archive_sha256=_archive_sha256(output),
        manifest=manifest,
    )


def _validated_manifest(
    raw: bytes,
    *,
    contracts_root: Path,
) -> dict[str, Any]:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _bundle_error() from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema_version",
            "dependency_lock",
            "dependencies",
            "files",
            "total_size",
        }
        or manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION
        or canonical_json_bytes(manifest) != raw
        or manifest.get("dependency_lock")
        != _dependency_lock_attestation(contracts_root)
    ):
        raise _bundle_error()

    lock = _dependency_lock(contracts_root)
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) != len(
        CONTRACT_DEPENDENCIES
    ):
        raise _bundle_error()
    expected_dependencies = []
    for dependency in CONTRACT_DEPENDENCIES:
        expected_dependencies.append(
            {
                "name": dependency["name"],
                "package": dependency["package"],
                "commit": dependency["commit"],
                "tree_sha256": lock[dependency["name"]]["tree_sha256"],
                "file_count": next(
                    (
                        item.get("file_count")
                        for item in dependencies
                        if isinstance(item, dict)
                        and item.get("name") == dependency["name"]
                    ),
                    None,
                ),
            }
        )
    if dependencies != expected_dependencies:
        raise _bundle_error()

    files = manifest.get("files")
    total_size = manifest.get("total_size")
    if (
        not isinstance(files, list)
        or not files
        or len(files) > MAX_BUNDLE_FILES
        or isinstance(total_size, bool)
        or not isinstance(total_size, int)
        or not 0 <= total_size <= MAX_BUNDLE_UNCOMPRESSED_BYTES
    ):
        raise _bundle_error()
    names: set[str] = set()
    counts = {item["name"]: 0 for item in CONTRACT_DEPENDENCIES}
    calculated_size = 0
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise _bundle_error()
        path = record.get("path")
        size = record.get("size")
        digest = record.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or path in names
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not _is_sha256(digest)
        ):
            raise _bundle_error()
        pure = PurePosixPath(path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise _bundle_error()
        if len(pure.parts) < 4 or pure.parts[:2] != ("contracts", "lib"):
            raise _bundle_error()
        dependency_name = pure.parts[2]
        if dependency_name not in counts:
            raise _bundle_error()
        names.add(path)
        counts[dependency_name] += 1
        calculated_size += size
    if calculated_size != total_size or any(
        counts[item["name"]] != item["file_count"] for item in dependencies
    ):
        raise _bundle_error()
    return manifest


def install_contract_dependency_bundle(
    archive: Path,
    *,
    expected_sha256: str,
    contracts_root: Path = CONTRACTS_ROOT,
) -> dict[str, Any]:
    """Verify a bundle completely, then atomically install its dependency tree."""

    root = _contracts_root(Path(contracts_root))
    source = Path(os.path.abspath(os.fspath(archive)))
    if not source.is_file() or not _is_sha256(expected_sha256):
        raise _bundle_error()
    actual_sha256 = _archive_sha256(source)
    if actual_sha256 != expected_sha256:
        raise _bundle_error()
    target_lib = root / "lib"
    if _is_link_or_reparse_point(target_lib):
        raise _bundle_error()
    if target_lib.exists() and (
        not target_lib.is_dir() or any(target_lib.iterdir())
    ):
        raise LoveEngineError(
            "contract_dependency_bundle_target_exists",
            "contracts/lib must be absent or empty",
            3,
        )

    stage = root / f".loveengine-dependency-bundle-{uuid.uuid4().hex}"
    installed = False
    try:
        stage.mkdir()
        with zipfile.ZipFile(source, "r") as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if (
                len(names) != len(set(names))
                or MANIFEST_NAME not in names
                or len(infos) > MAX_BUNDLE_FILES + 1
            ):
                raise _bundle_error()
            for info in infos:
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    info.is_dir()
                    or info.flag_bits & 1
                    or info.compress_type
                    not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or file_type not in {0, stat.S_IFREG}
                ):
                    raise _bundle_error()
            manifest_info = bundle.getinfo(MANIFEST_NAME)
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise _bundle_error()
            manifest = _validated_manifest(
                bundle.read(manifest_info),
                contracts_root=root,
            )
            expected_records = {item["path"]: item for item in manifest["files"]}
            if set(names) != {MANIFEST_NAME, *expected_records}:
                raise _bundle_error()
            for name in sorted(expected_records):
                record = expected_records[name]
                info = bundle.getinfo(name)
                if info.file_size != record["size"]:
                    raise _bundle_error()
                relative = PurePosixPath(name)
                destination = stage.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with bundle.open(info, "r") as raw, destination.open("xb") as target:
                    while chunk := raw.read(1024 * 1024):
                        size += len(chunk)
                        if size > record["size"]:
                            raise _bundle_error()
                        digest.update(chunk)
                        target.write(chunk)
                if (
                    size != record["size"]
                    or "sha256:" + digest.hexdigest() != record["sha256"]
                ):
                    raise _bundle_error()

        stage_lib = stage / "contracts" / "lib"
        for dependency in manifest["dependencies"]:
            inventory = _tree_inventory(
                stage_lib / dependency["name"],
                error_code="contract_dependency_bundle_invalid",
                detail=dependency["name"],
            )
            if (
                inventory["inventory_sha256"] != dependency["tree_sha256"]
                or inventory["count"] != dependency["file_count"]
            ):
                raise _bundle_error()
        if target_lib.exists():
            if any(target_lib.iterdir()):
                raise LoveEngineError(
                    "contract_dependency_bundle_target_exists",
                    "contracts/lib changed during installation",
                    3,
                )
            target_lib.rmdir()
        os.replace(stage_lib, target_lib)
        installed = True
        return _bundle_result(
            operation="install",
            archive=source,
            archive_sha256=actual_sha256,
            manifest=manifest,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise _bundle_error() from exc
    finally:
        if stage.exists():
            if installed:
                try:
                    (stage / "contracts").rmdir()
                    stage.rmdir()
                except OSError as exc:
                    raise _bundle_error() from exc
            else:
                _remove_owned_tree(
                    stage,
                    error_code="contract_dependency_bundle_invalid",
                    detail="bundle staging",
                )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("archive", type=Path)
    build.add_argument("--contracts-root", type=Path, default=CONTRACTS_ROOT)
    install = commands.add_parser("install")
    install.add_argument("archive", type=Path)
    install.add_argument("--contracts-root", type=Path, default=CONTRACTS_ROOT)
    install.add_argument("--expected-sha256", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "build":
            result = build_contract_dependency_bundle(
                args.archive,
                contracts_root=args.contracts_root,
            )
        else:
            result = install_contract_dependency_bundle(
                args.archive,
                expected_sha256=args.expected_sha256,
                contracts_root=args.contracts_root,
            )
    except LoveEngineError as error:
        print(json.dumps({"error": {"code": error.code}}, sort_keys=True))
        return error.exit_code
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
