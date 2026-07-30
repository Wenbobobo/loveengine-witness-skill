"""Pinned Foundry discovery and explicit contract preparation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import stat
import tempfile
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed


FOUNDRY_VERSION = "1.7.1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = REPOSITORY_ROOT / "contracts"
DEPENDENCY_LOCK_FILENAME = "dependency-lock.json"
PREPARATION_ATTESTATION_FILENAME = "loveengine-contract-preparation.json"
PREPARATION_ATTESTATION_SCHEMA = "loveengine.contract-preparation-attestation/1"
REQUIRED_CONTRACT_ARTIFACTS = (
    "WitnessDAO",
    "CorporateSink",
    "StreamingEngine",
    "PublicSink",
    "SkillRegistry",
)
CONTRACT_DEPENDENCIES = (
    {
        "name": "forge-std",
        "package": "foundry-rs/forge-std",
        "commit": "77041d2ce690e692d6e03cc812b57d1ddaa4d505",
        "repository_url": "https://github.com/foundry-rs/forge-std.git",
    },
    {
        "name": "openzeppelin-contracts",
        "package": "OpenZeppelin/openzeppelin-contracts",
        "commit": "e4f70216d759d8e6a64144a9e1f7bbeed78e7079",
        "repository_url": "https://github.com/OpenZeppelin/openzeppelin-contracts.git",
    },
)
# A dependency's root remote is not sufficient to constrain `git submodule
# update`: URLs and gitlinks declared by the pinned tree are another network
# boundary.  Keep the currently required graph small and explicit.  Adding a
# dependency with a deeper graph requires an intentional policy update rather
# than recursive discovery at prepare time.
CONTRACT_DEPENDENCY_SUBMODULES: dict[str, tuple[dict[str, str], ...]] = {
    "forge-std": (),
    "openzeppelin-contracts": (
        {
            "name": "lib/forge-std",
            "path": "lib/forge-std",
            "repository_url": "https://github.com/foundry-rs/forge-std",
            "commit": "1eea5bae12ae557d589f9f0f0edae2faa47cb262",
            "branch": "v1",
        },
        {
            "name": "lib/erc4626-tests",
            "path": "lib/erc4626-tests",
            "repository_url": "https://github.com/a16z/erc4626-tests.git",
            "commit": "8b1d7c2ac248c33c3506b1bff8321758943c5e11",
        },
        {
            "name": "lib/halmos-cheatcodes",
            "path": "lib/halmos-cheatcodes",
            "repository_url": "https://github.com/a16z/halmos-cheatcodes",
            "commit": "c0d865508c0fee0a11b97732c5e90f9cad6b65a5",
        },
    ),
}
REQUIRED_CONTRACT_SOURCES = {
    name: f"src/{name}.sol" for name in REQUIRED_CONTRACT_ARTIFACTS
}
EXPECTED_FOUNDRY_PROFILE_PATHS = {
    "src": "src",
    "test": "test",
    "script": "script",
    "out": "out",
    "libs": ["lib"],
}
EXPECTED_REMAPPINGS = (
    "forge-std/=lib/forge-std/src/",
    "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/",
)
ALLOWED_ARTIFACT_SOURCE_PREFIXES = (
    "src/",
    "lib/forge-std/",
    "lib/openzeppelin-contracts/",
)
FORBIDDEN_FOUNDRY_RESOLVER_KEYS = frozenset(
    {
        "allow_paths",
        "auto_detect_remappings",
        "base_path",
        "include_paths",
        "remappings",
    }
)
HEX_CHARACTERS = frozenset("0123456789abcdefABCDEF")

CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
BinaryLookup = Callable[[str], Path]


def foundry_binary(name: str) -> Path:
    """Return a deterministic Foundry binary path without checking its version."""

    if name not in {"forge", "anvil"}:
        raise LoveEngineError("invalid_foundry_binary", name)
    suffix = ".exe" if os.name == "nt" else ""
    executable = name + suffix
    configured = os.environ.get("FOUNDRY_BIN")
    candidates = [
        Path(configured) / executable if configured else None,
        Path.home()
        / ".codex"
        / "tools"
        / f"foundry-v{FOUNDRY_VERSION}"
        / executable,
        Path.home() / ".foundry" / "bin" / executable,
    ]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise LoveEngineError(
        "foundry_not_found",
        f"Foundry {FOUNDRY_VERSION} {name} is required; set FOUNDRY_BIN",
        3,
    )


def is_exact_foundry_version(name: str, output: str) -> bool:
    """Accept only direct Foundry output whose first non-empty line is pinned."""

    if name not in {"forge", "anvil"}:
        return False
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return bool(lines and lines[0] == f"{name} Version: {FOUNDRY_VERSION}")


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    error_code: str,
    error_message: str,
    command_runner: CommandRunner | None,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        if command_runner is not None:
            completed = command_runner(command, cwd)
        else:
            completed = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=1_200,
                env=environment,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LoveEngineError(error_code, error_message, 4) from exc
    if completed.returncode != 0:
        raise LoveEngineError(error_code, error_message, 4)
    return completed.stdout or ""


def _git_checkout_environment() -> dict[str, str]:
    """Prevent a host Git autocrlf policy from changing pinned source bytes."""

    environment = os.environ.copy()
    for key in tuple(environment):
        lowered = key.casefold()
        if lowered.startswith("git_") or lowered.startswith("ssh_askpass"):
            environment.pop(key)
    environment.update(
        {
            # Do not inherit a host URL rewrite, template, hook, or line-ending
            # policy while materializing a pinned public dependency.
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "https",
        }
    )
    return environment


def _forge_project_environment() -> dict[str, str]:
    """Run Forge against the validated default profile, not caller overrides."""

    environment = _git_checkout_environment()
    for key in tuple(environment):
        if key.startswith(("FOUNDRY_", "DAPP_")):
            environment.pop(key)
    environment["FOUNDRY_PROFILE"] = "default"
    environment["FOUNDRY_AUTO_DETECT_REMAPPINGS"] = "false"
    return environment


def _verified_toolchain(
    *,
    contracts_root: Path,
    binary_lookup: BinaryLookup,
    command_runner: CommandRunner | None,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name in ("forge", "anvil"):
        binary = binary_lookup(name)
        output = _run_command(
            [str(binary), "--version"],
            cwd=contracts_root,
            error_code="foundry_version_check_failed",
            error_message=f"could not check {name} version",
            command_runner=command_runner,
        )
        if not is_exact_foundry_version(name, output):
            raise LoveEngineError(
                "foundry_version_mismatch",
                f"{name} must report Foundry {FOUNDRY_VERSION}",
                3,
            )
        result[name] = {
            "path": str(binary),
            "version": f"{name} Version: {FOUNDRY_VERSION}",
        }
    return result


def _absolute_lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_link_or_reparse_point(path: Path) -> bool:
    """Identify POSIX links plus Windows junctions and other reparse points."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LoveEngineError("contract_path_unreadable", "contract path", 3) from exc
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_point and attributes & reparse_point)


def _reject_symlink_ancestors(path: Path) -> None:
    current = path
    while True:
        if _is_link_or_reparse_point(current):
            raise LoveEngineError("contract_path_symlink", "contract path contains a symlink", 3)
        if current.parent == current:
            return
        current = current.parent


def _contracts_root(path: Path) -> Path:
    root = _absolute_lexical_path(Path(path))
    _reject_symlink_ancestors(root)
    if not root.is_dir():
        raise LoveEngineError("contracts_root_missing", "contracts", 3)
    return root


def _contract_path(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    _reject_symlink_ancestors(path)
    return path


def _validate_dependency_layout(
    contracts_root: Path,
    *,
    require_complete: bool,
    allowed_transient_names: frozenset[str] = frozenset(),
) -> None:
    lib_root = _contract_path(contracts_root, "lib")
    if not lib_root.exists():
        if require_complete:
            raise LoveEngineError("contract_dependency_missing", "lib", 3)
        return
    if _is_link_or_reparse_point(lib_root) or not lib_root.is_dir():
        raise LoveEngineError("contract_dependency_missing", "lib", 3)
    try:
        children = list(lib_root.iterdir())
    except OSError as exc:
        raise LoveEngineError("contract_path_unreadable", "contract path", 3) from exc
    expected = {item["name"] for item in CONTRACT_DEPENDENCIES}
    names = {child.name for child in children}
    allowed = expected | allowed_transient_names
    if (require_complete and not expected.issubset(names)) or (
        not names.issubset(allowed)
    ):
        raise LoveEngineError("contract_dependency_layout_invalid", "lib", 3)
    for child in children:
        if _is_link_or_reparse_point(child) or not child.is_dir():
            raise LoveEngineError("contract_dependency_layout_invalid", "lib", 3)


def _safe_tree_files(
    root: Path,
    *,
    error_code: str,
    detail: str,
    skipped_names: frozenset[str] = frozenset(),
) -> list[Path]:
    """Walk regular files without descending into links or reparse points."""

    if _is_link_or_reparse_point(root) or not root.is_dir():
        raise LoveEngineError(error_code, detail, 3)
    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise LoveEngineError(error_code, detail, 3) from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_reparse_point(path):
                raise LoveEngineError(error_code, detail, 3)
            if entry.name in skipped_names:
                # Dependency Git metadata is neither compiler input nor part of
                # the locked source tree. Do not inspect or follow it.
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    raise LoveEngineError(error_code, detail, 3)
            except OSError as exc:
                raise LoveEngineError(error_code, detail, 3) from exc
    return sorted(files)


def _reject_link_or_reparse_tree(path: Path) -> None:
    if not path.exists():
        return
    _safe_tree_files(
        path,
        error_code="contract_path_symlink",
        detail="contract path contains a symlink",
    )


def _remove_owned_tree(path: Path, *, error_code: str, detail: str) -> None:
    """Remove a module-owned regular tree, including read-only Git objects."""

    if _is_link_or_reparse_point(path) or not path.is_dir():
        raise LoveEngineError(error_code, detail, 3)
    _reject_link_or_reparse_tree(path)

    def make_writable(operation: Any, raw_path: str, exc_info: Any) -> None:
        try:
            os.chmod(raw_path, stat.S_IREAD | stat.S_IWRITE)
            operation(raw_path)
        except OSError:
            raise exc_info[1]

    try:
        shutil.rmtree(path, onerror=make_writable)
    except OSError as exc:
        raise LoveEngineError(error_code, detail, 3) from exc
    if path.exists():
        raise LoveEngineError(error_code, detail, 3)


def _remove_link_or_reparse_entry(
    path: Path,
    *,
    error_code: str,
    detail: str,
) -> None:
    """Unlink a nested link/reparse entry without dereferencing it."""

    last_error: OSError | None = None
    for operation in (os.unlink, os.rmdir):
        try:
            operation(path)
            return
        except (IsADirectoryError, NotADirectoryError, PermissionError) as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
    raise LoveEngineError(error_code, detail, 3) from last_error


def _remove_fresh_checkout_tree(
    path: Path,
    *,
    error_code: str,
    detail: str,
) -> None:
    """Delete a newly owned checkout without traversing nested links."""

    if _is_link_or_reparse_point(path) or not path.is_dir():
        raise LoveEngineError(error_code, detail, 3)

    def remove_directory(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise LoveEngineError(error_code, detail, 3) from exc
        for entry in entries:
            child = Path(entry.path)
            if _is_link_or_reparse_point(child):
                _remove_link_or_reparse_entry(
                    child,
                    error_code=error_code,
                    detail=detail,
                )
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    remove_directory(child)
                elif entry.is_file(follow_symlinks=False):
                    _remove_owned_file(
                        child,
                        error_code=error_code,
                        detail=detail,
                    )
                else:
                    raise LoveEngineError(error_code, detail, 3)
            except OSError as exc:
                raise LoveEngineError(error_code, detail, 3) from exc
        try:
            os.rmdir(directory)
        except PermissionError:
            try:
                os.chmod(directory, stat.S_IREAD | stat.S_IWRITE)
                os.rmdir(directory)
            except OSError as exc:
                raise LoveEngineError(error_code, detail, 3) from exc
        except OSError as exc:
            raise LoveEngineError(error_code, detail, 3) from exc

    remove_directory(path)
    if path.exists() or _is_link_or_reparse_point(path):
        raise LoveEngineError(error_code, detail, 3)


def _remove_owned_file(path: Path, *, error_code: str, detail: str) -> None:
    """Remove a module-owned regular file even when Git marked it read-only."""

    if _is_link_or_reparse_point(path) or not path.is_file():
        raise LoveEngineError(error_code, detail, 3)
    try:
        path.unlink()
    except PermissionError:
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            path.unlink()
        except OSError as exc:
            raise LoveEngineError(error_code, detail, 3) from exc
    except OSError as exc:
        raise LoveEngineError(error_code, detail, 3) from exc
    if path.exists():
        raise LoveEngineError(error_code, detail, 3)


def _strip_dependency_git_metadata(
    root: Path,
    *,
    error_code: str,
    detail: str,
) -> None:
    """Remove root and submodule Git metadata before the tree becomes usable."""

    if _is_link_or_reparse_point(root) or not root.is_dir():
        raise LoveEngineError(error_code, detail, 3)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise LoveEngineError(error_code, detail, 3) from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_reparse_point(path):
                raise LoveEngineError(error_code, detail, 3)
            if entry.name == ".git":
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _remove_owned_tree(path, error_code=error_code, detail=detail)
                    elif entry.is_file(follow_symlinks=False):
                        _remove_owned_file(path, error_code=error_code, detail=detail)
                    else:
                        raise LoveEngineError(error_code, detail, 3)
                except OSError as exc:
                    raise LoveEngineError(error_code, detail, 3) from exc
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif not entry.is_file(follow_symlinks=False):
                    raise LoveEngineError(error_code, detail, 3)
            except OSError as exc:
                raise LoveEngineError(error_code, detail, 3) from exc
    if (root / ".git").exists() or _is_link_or_reparse_point(root / ".git"):
        raise LoveEngineError(error_code, detail, 3)


def _refresh_backup_name(name: str, dependency: str) -> bool:
    """Recognize only backups created by this module's staged replacement."""

    prefix = f".loveengine-{dependency}-backup-"
    suffix = name.removeprefix(prefix)
    return (
        name.startswith(prefix)
        and len(suffix) == 32
        and all(character in "0123456789abcdef" for character in suffix)
    )


def _refresh_backup_names(contracts_root: Path) -> frozenset[str]:
    """List syntactically owned refresh backups without accepting other entries."""

    lib_root = _contract_path(contracts_root, "lib")
    if not lib_root.exists() or _is_link_or_reparse_point(lib_root) or not lib_root.is_dir():
        return frozenset()
    try:
        children = list(lib_root.iterdir())
    except OSError as exc:
        raise LoveEngineError("contract_path_unreadable", "contract path", 3) from exc
    return frozenset(
        child.name
        for child in children
        if any(
            _refresh_backup_name(child.name, dependency["name"])
            for dependency in CONTRACT_DEPENDENCIES
        )
    )


def _preflight_prepare_paths(
    contracts_root: Path,
    *,
    allowed_transient_names: frozenset[str] = frozenset(),
) -> None:
    """Reject every active workspace boundary before invoking Forge."""

    for relative in (
        "src",
        "out",
        "lib",
        "cache",
        "foundry.toml",
        "remappings.txt",
        DEPENDENCY_LOCK_FILENAME,
    ):
        path = _contract_path(contracts_root, relative)
        if relative in {"out", "lib", "cache"}:
            _reject_link_or_reparse_tree(path)
        if relative == "lib":
            _validate_dependency_layout(
                contracts_root,
                require_complete=False,
                allowed_transient_names=allowed_transient_names,
            )


def _recover_interrupted_dependency_refresh(
    contracts_root: Path,
    *,
    dependency_lock: dict[str, dict[str, str]],
) -> None:
    """Recover a crash between the two owned directory replacements.

    A backup is retained until the new pinned tree has been built. On the next
    preparation run it is either restored when the active directory is absent,
    or removed only after the active directory independently matches the lock.
    """

    lib_root = _contract_path(contracts_root, "lib")
    if not lib_root.exists():
        return
    if _is_link_or_reparse_point(lib_root) or not lib_root.is_dir():
        raise LoveEngineError("contract_dependency_missing", "lib", 3)
    try:
        children = list(lib_root.iterdir())
    except OSError as exc:
        raise LoveEngineError("contract_path_unreadable", "contract path", 3) from exc

    expected = {item["name"] for item in CONTRACT_DEPENDENCIES}
    backup_by_dependency: dict[str, list[Path]] = {name: [] for name in expected}
    for child in children:
        if child.name in expected:
            if _is_link_or_reparse_point(child) or not child.is_dir():
                raise LoveEngineError("contract_dependency_layout_invalid", "lib", 3)
            continue
        matching = [
            dependency
            for dependency in expected
            if _refresh_backup_name(child.name, dependency)
        ]
        if len(matching) != 1 or _is_link_or_reparse_point(child) or not child.is_dir():
            raise LoveEngineError("contract_dependency_layout_invalid", "lib", 3)
        backup_by_dependency[matching[0]].append(child)

    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        backups = backup_by_dependency[name]
        if len(backups) > 1:
            raise LoveEngineError(
                "contract_dependency_refresh_recovery_required", name, 3
            )
        if not backups:
            continue
        backup = backups[0]
        target = _contract_path(contracts_root, "lib", name)
        _reject_link_or_reparse_tree(backup)
        if not target.exists():
            try:
                os.replace(backup, target)
            except OSError as exc:
                raise LoveEngineError(
                    "contract_dependency_refresh_recovery_failed", name, 3
                ) from exc
            continue
        if not _dependency_matches_lock(
            contracts_root,
            dependency=dependency,
            expected_tree_sha256=dependency_lock[name]["tree_sha256"],
        ):
            raise LoveEngineError(
                "contract_dependency_refresh_recovery_required", name, 3
            )
        _remove_owned_tree(
            backup,
            error_code="contract_dependency_refresh_cleanup_failed",
            detail=name,
        )


def _stable_file_bytes(
    path: Path,
    *,
    error_code: str,
    detail: str,
) -> tuple[bytes, bytes]:
    try:
        contents = path.read_bytes()
    except OSError as exc:
        raise LoveEngineError(error_code, detail, 3) from exc
    # Dependencies contain source-bearing extensionless files such as
    # .gitmodules and LICENSE. Treat UTF-8, NUL-free content as text so a
    # host Git autocrlf policy cannot change the locked inventory.
    if b"\x00" not in contents:
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return contents, contents.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return contents, contents


def _inventory(
    root: Path,
    paths: list[Path],
    *,
    error_code: str,
    detail: str,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    raw_files: list[dict[str, Any]] = []
    # Path ordering is host-dependent around case.  The lock is exchanged
    # between Windows and Linux, so sort its records by the archive-style,
    # case-sensitive POSIX relative path rather than by Path itself.
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if _is_link_or_reparse_point(path) or not path.is_file():
            raise LoveEngineError(error_code, detail, 3)
        raw_contents, contents = _stable_file_bytes(
            path,
            error_code=error_code,
            detail=detail,
        )
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(contents),
                "sha256": sha256_prefixed(contents),
            }
        )
        raw_files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(raw_contents),
                "sha256": sha256_prefixed(raw_contents),
            }
        )
    if not files:
        raise LoveEngineError(error_code, detail, 3)
    return {
        "count": len(files),
        "inventory_sha256": sha256_prefixed(canonical_json_bytes(files)),
        "raw_inventory_sha256": sha256_prefixed(canonical_json_bytes(raw_files)),
        "files": files,
        "raw_files": raw_files,
    }


def _tree_inventory(root: Path, *, error_code: str, detail: str) -> dict[str, Any]:
    files = _safe_tree_files(
        root,
        error_code=error_code,
        detail=detail,
        skipped_names=frozenset({".git"}),
    )
    return _inventory(root, files, error_code=error_code, detail=detail)


def _validated_foundry_project_configuration(contracts_root: Path) -> None:
    """Constrain Forge to the local source, dependency, and output boundaries."""

    foundry_toml = _contract_path(contracts_root, "foundry.toml")
    remappings = _contract_path(contracts_root, "remappings.txt")
    try:
        configuration = tomllib.loads(foundry_toml.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LoveEngineError("contract_foundry_config_invalid", "foundry.toml", 3) from exc
    profile = configuration.get("profile")
    default_profile = profile.get("default") if isinstance(profile, dict) else None
    if not isinstance(default_profile, dict) or any(
        default_profile.get(key) != expected
        for key, expected in EXPECTED_FOUNDRY_PROFILE_PATHS.items()
    ):
        raise LoveEngineError("contract_foundry_config_invalid", "foundry.toml", 3)
    if any(
        key in mapping
        for mapping in (configuration, default_profile)
        for key in FORBIDDEN_FOUNDRY_RESOLVER_KEYS
    ):
        raise LoveEngineError("contract_foundry_config_invalid", "foundry.toml", 3)
    try:
        normalized_remappings = remappings.read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        ).replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise LoveEngineError(
            "contract_foundry_config_invalid", "remappings.txt", 3
        ) from exc
    lines = tuple(line for line in normalized_remappings.splitlines() if line)
    if lines != EXPECTED_REMAPPINGS:
        raise LoveEngineError("contract_foundry_config_invalid", "remappings.txt", 3)


def _contract_source_inventory(contracts_root: Path) -> dict[str, Any]:
    source_root = _contract_path(contracts_root, "src")
    if _is_link_or_reparse_point(source_root) or not source_root.is_dir():
        raise LoveEngineError("contract_sources_missing", "contracts/src", 3)
    for relative in REQUIRED_CONTRACT_SOURCES.values():
        source_path = _contract_path(contracts_root, *relative.split("/"))
        if not source_path.is_file():
            raise LoveEngineError("contract_sources_missing", relative, 3)
    files = [
        path
        for path in _safe_tree_files(
            source_root,
            error_code="contract_sources_missing",
            detail="contracts/src",
        )
        if path.suffix == ".sol"
    ]
    for relative in ("foundry.toml", "remappings.txt"):
        files.append(_contract_path(contracts_root, relative))
    return _inventory(
        contracts_root,
        files,
        error_code="contract_sources_missing",
        detail="contracts/src",
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len("sha256:") + 64
        and value.startswith("sha256:")
        and all(character in HEX_CHARACTERS for character in value[7:])
    )


def _read_json_object(path: Path, *, error_code: str, detail: str) -> tuple[bytes, dict[str, Any]]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise LoveEngineError(error_code, detail, 3) from exc
    if not isinstance(value, dict):
        raise LoveEngineError(error_code, detail, 3)
    return raw, value


def _dependency_lock(contracts_root: Path) -> dict[str, dict[str, str]]:
    path = _contract_path(contracts_root, DEPENDENCY_LOCK_FILENAME)
    if not path.is_file():
        raise LoveEngineError(
            "contract_dependency_lock_missing", DEPENDENCY_LOCK_FILENAME, 3
        )
    _, value = _read_json_object(
        path,
        error_code="contract_dependency_lock_invalid",
        detail=DEPENDENCY_LOCK_FILENAME,
    )
    if set(value) != {
        "schema_version",
        "tree_digest_algorithm",
        "dependencies",
    }:
        raise LoveEngineError(
            "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
        )
    if value.get("schema_version") != "loveengine.contract-dependency-lock/1":
        raise LoveEngineError(
            "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
        )
    if value.get("tree_digest_algorithm") != "sha256:canonical-source-tree-lf-v1":
        raise LoveEngineError(
            "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
        )
    entries = value.get("dependencies")
    if not isinstance(entries, list) or len(entries) != len(CONTRACT_DEPENDENCIES):
        raise LoveEngineError(
            "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
        )
    expected = {item["name"]: item for item in CONTRACT_DEPENDENCIES}
    locked: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "name",
            "package",
            "commit",
            "tree_sha256",
        }:
            raise LoveEngineError(
                "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
            )
        name = entry.get("name")
        if not isinstance(name, str) or name not in expected or name in locked:
            raise LoveEngineError(
                "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
            )
        pinned = expected[name]
        if (
            entry.get("package") != pinned["package"]
            or entry.get("commit") != pinned["commit"]
            or not _is_sha256(entry.get("tree_sha256"))
        ):
            raise LoveEngineError(
                "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
            )
        locked[name] = {
            "name": name,
            "package": str(entry["package"]),
            "commit": str(entry["commit"]),
            "tree_sha256": str(entry["tree_sha256"]),
        }
    if set(locked) != set(expected):
        raise LoveEngineError(
            "contract_dependency_lock_invalid", DEPENDENCY_LOCK_FILENAME, 3
        )
    return locked


def _dependency_lock_attestation(contracts_root: Path) -> dict[str, str]:
    """Bind the exact, validated lock bytes to a preparation record."""

    path = _contract_path(contracts_root, DEPENDENCY_LOCK_FILENAME)
    raw, _ = _read_json_object(
        path,
        error_code="contract_dependency_lock_invalid",
        detail=DEPENDENCY_LOCK_FILENAME,
    )
    return {
        "path": f"contracts/{DEPENDENCY_LOCK_FILENAME}",
        "sha256": sha256_prefixed(raw),
    }


def _dependency_inventories(
    contracts_root: Path,
    *,
    dependency_lock: dict[str, dict[str, str]],
    allowed_transient_names: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    _validate_dependency_layout(
        contracts_root,
        require_complete=True,
        allowed_transient_names=allowed_transient_names,
    )
    inventories: list[dict[str, Any]] = []
    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        path = _contract_path(contracts_root, "lib", name)
        inventory = _tree_inventory(
            path,
            error_code="contract_dependency_missing",
            detail=name,
        )
        if inventory["inventory_sha256"] != dependency_lock[name]["tree_sha256"]:
            raise LoveEngineError(
                "contract_dependency_lock_mismatch",
                f"{name}: run loveengine pilot contracts prepare --refresh-dependencies",
                3,
            )
        inventories.append(
            {
                "name": name,
                "package": dependency["package"],
                "commit": dependency["commit"],
                "tree_sha256": inventory["inventory_sha256"],
                "raw_tree_sha256": inventory["raw_inventory_sha256"],
                "file_count": inventory["count"],
                "files": inventory["files"],
                "raw_files": inventory["raw_files"],
            }
        )
    return inventories


def _is_deployment_bytecode(value: str) -> bool:
    return (
        value.startswith("0x")
        and len(value) > 2
        and len(value) % 2 == 0
        and all(character in HEX_CHARACTERS for character in value[2:])
    )


def _artifact_inventory(contracts_root: Path) -> dict[str, Any]:
    artifact_root = _contract_path(contracts_root, "out")
    if _is_link_or_reparse_point(artifact_root) or not artifact_root.is_dir():
        raise LoveEngineError(
            "contract_artifact_missing",
            "run loveengine pilot contracts prepare",
            3,
        )
    artifacts: list[dict[str, Any]] = []
    for name in REQUIRED_CONTRACT_ARTIFACTS:
        path = _contract_path(contracts_root, "out", f"{name}.sol", f"{name}.json")
        if not path.is_file():
            raise LoveEngineError(
                "contract_artifact_missing",
                f"{name}: run loveengine pilot contracts prepare",
                3,
            )
        try:
            contents = path.read_bytes()
            artifact = json.loads(contents.decode("utf-8"))
            abi = artifact["abi"]
            bytecode = artifact["bytecode"]["object"]
            deployed_bytecode = artifact["deployedBytecode"]["object"]
            compilation_target = artifact["metadata"]["settings"]["compilationTarget"]
            metadata_sources = artifact["metadata"]["sources"]
        except (
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise LoveEngineError("contract_artifact_invalid", name, 3) from exc
        if (
            not isinstance(abi, list)
            or not isinstance(bytecode, str)
            or not isinstance(deployed_bytecode, str)
            or compilation_target != {REQUIRED_CONTRACT_SOURCES[name]: name}
            or not isinstance(metadata_sources, dict)
            or not metadata_sources
            or not _is_deployment_bytecode(bytecode)
            or not _is_deployment_bytecode(deployed_bytecode)
        ):
            raise LoveEngineError("contract_artifact_invalid", name, 3)
        compiler_sources = sorted(metadata_sources)
        if (
            not all(isinstance(source, str) for source in compiler_sources)
            or REQUIRED_CONTRACT_SOURCES[name] not in compiler_sources
            or not all(
                source.startswith(ALLOWED_ARTIFACT_SOURCE_PREFIXES)
                for source in compiler_sources
            )
        ):
            raise LoveEngineError("contract_artifact_invalid", name, 3)
        artifacts.append(
            {
                "contract": name,
                "path": f"contracts/out/{name}.sol/{name}.json",
                "sha256": sha256_prefixed(contents),
                "bytecode_sha256": sha256_prefixed(bytecode.encode("ascii")),
                "deployed_bytecode_sha256": sha256_prefixed(
                    deployed_bytecode.encode("ascii")
                ),
                "compiler_sources": compiler_sources,
            }
        )
    return {
        "count": len(artifacts),
        "inventory_sha256": sha256_prefixed(canonical_json_bytes(artifacts)),
        "files": artifacts,
    }


def _cache_directory(contracts_root: Path) -> Path:
    return _contract_path(contracts_root, "cache")


def _normalize_file_to_lf(path: Path, *, dependency: str) -> None:
    raw, normalized = _stable_file_bytes(
        path,
        error_code="contract_dependency_normalization_failed",
        detail=dependency,
    )
    if raw == normalized:
        return
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".loveengine-normalize-",
            dir=path.parent,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as target:
            target.write(normalized)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise LoveEngineError(
            "contract_dependency_normalization_failed", dependency, 3
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _normalize_dependency_tree_to_lf(root: Path, *, dependency: str) -> None:
    for path in _safe_tree_files(
        root,
        error_code="contract_dependency_missing",
        detail=dependency,
        skipped_names=frozenset({".git"}),
    ):
        _normalize_file_to_lf(path, dependency=dependency)


def _dependency_matches_lock(
    contracts_root: Path,
    *,
    dependency: dict[str, str],
    expected_tree_sha256: str,
) -> bool:
    path = _contract_path(contracts_root, "lib", dependency["name"])
    if not path.is_dir():
        return False
    inventory = _tree_inventory(
        path,
        error_code="contract_dependency_missing",
        detail=dependency["name"],
    )
    return inventory["inventory_sha256"] == expected_tree_sha256


def _configured_dependency(dependency: dict[str, str]) -> dict[str, str]:
    """Return the fixed source definition; never derive a remote URL from input."""

    for configured in CONTRACT_DEPENDENCIES:
        if all(
            dependency.get(key) == configured[key]
            for key in ("name", "package", "commit")
        ):
            return configured
    raise LoveEngineError("contract_dependency_install_failed", "dependency", 3)


def _expected_submodule_config_bytes(name: str) -> bytes:
    """Render the exact .gitmodules file allowed for one pinned dependency."""

    try:
        submodules = CONTRACT_DEPENDENCY_SUBMODULES[name]
    except KeyError as exc:
        raise LoveEngineError("contract_dependency_install_failed", name, 3) from exc
    lines: list[str] = []
    for submodule in submodules:
        lines.append(f'[submodule "{submodule["name"]}"]')
        if "branch" in submodule:
            lines.append(f'\tbranch = {submodule["branch"]}')
        lines.append(f'\tpath = {submodule["path"]}')
        lines.append(f'\turl = {submodule["repository_url"]}')
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _validated_dependency_submodules(
    target: Path,
    *,
    name: str,
    command_runner: CommandRunner | None,
    environment: dict[str, str],
) -> tuple[dict[str, str], ...]:
    """Check allowed paths, URLs, and gitlinks before submodule network I/O."""

    try:
        submodules = CONTRACT_DEPENDENCY_SUBMODULES[name]
    except KeyError as exc:
        raise LoveEngineError("contract_dependency_install_failed", name, 3) from exc
    config = target / ".gitmodules"
    if not submodules:
        if config.exists() or _is_link_or_reparse_point(config):
            raise LoveEngineError("contract_dependency_install_failed", name, 3)
        return submodules
    if _is_link_or_reparse_point(config) or not config.is_file():
        raise LoveEngineError("contract_dependency_install_failed", name, 3)
    try:
        actual_config = config.read_bytes()
    except OSError as exc:
        raise LoveEngineError("contract_dependency_install_failed", name, 3) from exc
    if actual_config != _expected_submodule_config_bytes(name):
        raise LoveEngineError("contract_dependency_install_failed", name, 3)
    for submodule in submodules:
        gitlink = _run_command(
            ["git", "ls-tree", "HEAD", "--", submodule["path"]],
            cwd=target,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        expected_gitlink = (
            f"160000 commit {submodule['commit']}\t{submodule['path']}"
        )
        if gitlink.strip() != expected_gitlink:
            raise LoveEngineError("contract_dependency_install_failed", name, 3)
    return submodules


def _reject_nested_submodule_declarations(target: Path, *, name: str) -> None:
    """Make a deeper source graph an explicit policy change, not a download."""

    for path in _safe_tree_files(
        target,
        error_code="contract_dependency_install_failed",
        detail=name,
        skipped_names=frozenset({".git"}),
    ):
        if (
            path.name == ".gitmodules"
            and path.relative_to(target).as_posix() != ".gitmodules"
        ):
            raise LoveEngineError("contract_dependency_install_failed", name, 3)


def _install_dependency(
    *,
    cwd: Path,
    dependency: dict[str, str],
    expected_tree_sha256: str,
    command_runner: CommandRunner | None,
) -> None:
    """Materialize one exact public Git commit and verify it before use.

    A clean Linux CI checkout materialized a tree that did not match the
    versioned lock through Forge's installer. This path fetches the locked
    object by its full SHA from a fixed root URL, then permits only the
    versioned submodule graph declared above before accepting the source tree.
    """

    configured = _configured_dependency(dependency)
    name = configured["name"]
    lib_root = _contract_path(cwd, "lib")
    target = _contract_path(cwd, "lib", name)
    created = False
    installed = False
    try:
        if lib_root.exists() and (
            _is_link_or_reparse_point(lib_root) or not lib_root.is_dir()
        ):
            raise LoveEngineError("contract_dependency_missing", "lib", 3)
        lib_root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse_point(lib_root) or not lib_root.is_dir():
            raise LoveEngineError("contract_dependency_missing", "lib", 3)
        if target.exists() or _is_link_or_reparse_point(target):
            raise LoveEngineError("contract_dependency_install_failed", name, 3)

        # The target was absent and is now owned by this installation attempt,
        # including the case where Git creates it before reporting an error.
        created = True
        environment = _git_checkout_environment()
        _run_command(
            ["git", "init", "--quiet", str(target)],
            cwd=cwd,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        if _is_link_or_reparse_point(target) or not target.is_dir():
            raise LoveEngineError("contract_dependency_install_failed", name, 3)
        _run_command(
            ["git", "remote", "add", "origin", configured["repository_url"]],
            cwd=target,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        _run_command(
            [
                "git",
                "fetch",
                "--depth",
                "1",
                "--no-tags",
                "origin",
                configured["commit"],
            ],
            cwd=target,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        _run_command(
            ["git", "checkout", "--detach", "--force", "FETCH_HEAD"],
            cwd=target,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        head = _run_command(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            error_code="contract_dependency_install_failed",
            error_message=f"could not install {name}",
            command_runner=command_runner,
            environment=environment,
        )
        if head.strip().lower() != configured["commit"]:
            raise LoveEngineError("contract_dependency_install_failed", name, 3)
        submodules = _validated_dependency_submodules(
            target,
            name=name,
            command_runner=command_runner,
            environment=environment,
        )
        for submodule in submodules:
            _run_command(
                [
                    "git",
                    "submodule",
                    "update",
                    "--init",
                    "--depth",
                    "1",
                    "--",
                    submodule["path"],
                ],
                cwd=target,
                error_code="contract_dependency_install_failed",
                error_message=f"could not install {name}",
                command_runner=command_runner,
                environment=environment,
            )
        _reject_nested_submodule_declarations(target, name=name)
        _strip_dependency_git_metadata(
            target,
            error_code="contract_dependency_install_failed",
            detail=name,
        )
        _normalize_dependency_tree_to_lf(target, dependency=name)
        inventory = _tree_inventory(
            target,
            error_code="contract_dependency_install_failed",
            detail=name,
        )
        if inventory["inventory_sha256"] != expected_tree_sha256:
            raise LoveEngineError(
                "contract_dependency_lock_mismatch",
                f"{name}: dependency does not match dependency-lock.json",
                3,
            )
        installed = True
    finally:
        # A failed fresh checkout must not leave a partial dependency that a
        # later prepare run could mistake for an operator-provided tree.
        if created and not installed and (
            target.exists() or _is_link_or_reparse_point(target)
        ):
            _remove_fresh_checkout_tree(
                target,
                error_code="contract_dependency_install_failed",
                detail=name,
            )


def _refresh_dependency(
    contracts_root: Path,
    *,
    dependency: dict[str, str],
    expected_tree_sha256: str,
    command_runner: CommandRunner | None,
) -> tuple[Path, Path | None]:
    """Stage and verify one replacement before moving an existing lib aside."""

    name = dependency["name"]
    lib_root = _contract_path(contracts_root, "lib")
    if not lib_root.is_dir():
        raise LoveEngineError("contract_dependency_missing", "lib", 3)
    target = _contract_path(contracts_root, "lib", name)
    stage: Path | None = None
    backup: Path | None = None
    replaced = False
    try:
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".loveengine-{name}-refresh-",
                dir=contracts_root,
            )
        )
        _install_dependency(
            cwd=stage,
            dependency=dependency,
            expected_tree_sha256=expected_tree_sha256,
            command_runner=command_runner,
        )
        staged = stage / "lib" / name
        _reject_symlink_ancestors(staged)
        backup = lib_root / f".loveengine-{name}-backup-{uuid.uuid4().hex}"
        if backup.exists() or _is_link_or_reparse_point(backup):
            raise LoveEngineError("contract_dependency_refresh_failed", name, 3)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(staged, target)
            replaced = True
        except OSError as exc:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise LoveEngineError("contract_dependency_refresh_failed", name, 3) from exc
    except LoveEngineError:
        raise
    except OSError as exc:
        raise LoveEngineError("contract_dependency_refresh_failed", name, 3) from exc
    finally:
        if not replaced and backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)
        if stage is not None and stage.exists():
            _remove_owned_tree(
                stage,
                error_code="contract_dependency_refresh_failed",
                detail=name,
            )
    return target, backup


def _current_attestation(contracts_root: Path) -> dict[str, Any]:
    # Check cache eagerly so every verification covers root, out, lib, and cache.
    _cache_directory(contracts_root)
    _validated_foundry_project_configuration(contracts_root)
    dependency_lock = _dependency_lock(contracts_root)
    return {
        "schema_version": PREPARATION_ATTESTATION_SCHEMA,
        "dependency_lock": _dependency_lock_attestation(contracts_root),
        "contract_source": _contract_source_inventory(contracts_root),
        "dependencies": _dependency_inventories(
            contracts_root,
            dependency_lock=dependency_lock,
        ),
        "artifacts": _artifact_inventory(contracts_root),
    }


def _attestation_path(contracts_root: Path) -> Path:
    return _contract_path(
        contracts_root,
        "cache",
        PREPARATION_ATTESTATION_FILENAME,
    )


def _write_attestation(contracts_root: Path, value: dict[str, Any]) -> dict[str, str]:
    cache = _cache_directory(contracts_root)
    if cache.exists() and not cache.is_dir():
        raise LoveEngineError(
            "contract_preparation_attestation_write_failed", "cache", 3
        )
    try:
        cache.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise LoveEngineError(
            "contract_preparation_attestation_write_failed", "cache", 3
        ) from exc
    _reject_symlink_ancestors(cache)
    path = _attestation_path(contracts_root)
    if path.exists() and not path.is_file():
        raise LoveEngineError(
            "contract_preparation_attestation_write_failed",
            PREPARATION_ATTESTATION_FILENAME,
            3,
        )
    contents = canonical_json_bytes(value)
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".loveengine-contract-preparation-",
            dir=cache,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as target:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise LoveEngineError(
            "contract_preparation_attestation_write_failed",
            PREPARATION_ATTESTATION_FILENAME,
            3,
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "path": f"contracts/cache/{PREPARATION_ATTESTATION_FILENAME}",
        "sha256": sha256_prefixed(contents),
    }


def _verify_attestation(contracts_root: Path, expected: dict[str, Any]) -> dict[str, str]:
    path = _attestation_path(contracts_root)
    if not path.is_file():
        raise LoveEngineError(
            "contract_preparation_attestation_missing",
            PREPARATION_ATTESTATION_FILENAME,
            3,
        )
    raw, value = _read_json_object(
        path,
        error_code="contract_preparation_attestation_invalid",
        detail=PREPARATION_ATTESTATION_FILENAME,
    )
    canonical = canonical_json_bytes(value)
    if raw != canonical:
        raise LoveEngineError(
            "contract_preparation_attestation_invalid",
            PREPARATION_ATTESTATION_FILENAME,
            3,
        )
    if value != expected:
        raise LoveEngineError(
            "contract_preparation_attestation_mismatch",
            PREPARATION_ATTESTATION_FILENAME,
            3,
        )
    return {
        "path": f"contracts/cache/{PREPARATION_ATTESTATION_FILENAME}",
        "sha256": sha256_prefixed(canonical),
    }


def _dependency_summary(
    inventories: list[dict[str, Any]],
    installed: dict[str, bool],
) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "package": item["package"],
            "commit": item["commit"],
            "path": f"contracts/lib/{item['name']}",
            "installed": installed[item["name"]],
            "tree_sha256": item["tree_sha256"],
            "file_count": item["file_count"],
        }
        for item in inventories
    ]


def verify_prepared_contract_artifacts(
    contracts_root: Path = CONTRACTS_ROOT,
) -> dict[str, Any]:
    """Require an unmodified, attested set of active Pilot deployment artifacts."""

    root = _contracts_root(Path(contracts_root))
    attestation = _current_attestation(root)
    verified = _verify_attestation(root, attestation)
    return {
        **attestation["artifacts"],
        "contract_source_sha256": attestation["contract_source"]["inventory_sha256"],
        "dependencies": _dependency_summary(
            attestation["dependencies"],
            {item["name"]: False for item in attestation["dependencies"]},
        ),
        "attestation": verified,
    }


def prepare_contracts(
    contracts_root: Path = CONTRACTS_ROOT,
    *,
    binary_lookup: BinaryLookup = foundry_binary,
    command_runner: CommandRunner | None = None,
    refresh_dependencies: bool = False,
) -> dict[str, Any]:
    """Install locked dependencies, build once, and persist canonical provenance."""

    root = _contracts_root(Path(contracts_root))
    _preflight_prepare_paths(
        root,
        allowed_transient_names=_refresh_backup_names(root),
    )
    _validated_foundry_project_configuration(root)
    toolchain = _verified_toolchain(
        contracts_root=root,
        binary_lookup=binary_lookup,
        command_runner=command_runner,
    )
    dependency_lock = _dependency_lock(root)
    _recover_interrupted_dependency_refresh(root, dependency_lock=dependency_lock)
    _preflight_prepare_paths(root)
    forge = Path(toolchain["forge"]["path"])
    lib_root = _contract_path(root, "lib")
    if lib_root.exists() and not lib_root.is_dir():
        raise LoveEngineError("contract_dependency_missing", "lib", 3)
    installed = {dependency["name"]: False for dependency in CONTRACT_DEPENDENCIES}
    refreshed = {dependency["name"]: False for dependency in CONTRACT_DEPENDENCIES}
    refreshed_backups: list[Path] = []
    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        path = _contract_path(root, "lib", name)
        if path.exists() and not path.is_dir():
            raise LoveEngineError("contract_dependency_missing", name, 3)
        if not path.is_dir():
            _install_dependency(
                cwd=root,
                dependency=dependency,
                expected_tree_sha256=dependency_lock[name]["tree_sha256"],
                command_runner=command_runner,
            )
            installed[name] = True
        elif not _dependency_matches_lock(
            root,
            dependency=dependency,
            expected_tree_sha256=dependency_lock[name]["tree_sha256"],
        ):
            if not refresh_dependencies:
                raise LoveEngineError(
                    "contract_dependency_lock_mismatch",
                    f"{name}: run loveengine pilot contracts prepare --refresh-dependencies",
                    3,
                )
            _, backup = _refresh_dependency(
                root,
                dependency=dependency,
                expected_tree_sha256=dependency_lock[name]["tree_sha256"],
                command_runner=command_runner,
            )
            if backup is not None:
                refreshed_backups.append(backup)
            refreshed[name] = True
    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        _normalize_dependency_tree_to_lf(
            _contract_path(root, "lib", name), dependency=name
        )
    # All managed dependencies are canonicalized before compiling so source
    # metadata cannot depend on a host Git line-ending policy.
    _reject_link_or_reparse_tree(_contract_path(root, "lib"))
    _reject_link_or_reparse_tree(_contract_path(root, "out"))
    _reject_link_or_reparse_tree(_contract_path(root, "cache"))
    _dependency_inventories(
        root,
        dependency_lock=dependency_lock,
        allowed_transient_names=frozenset(backup.name for backup in refreshed_backups),
    )
    _run_command(
        [str(forge), "build", "--force", "--threads", "1"],
        cwd=root,
        error_code="contract_build_failed",
        error_message="pinned Forge build failed",
        command_runner=command_runner,
        environment=_forge_project_environment(),
    )
    for backup in refreshed_backups:
        _remove_owned_tree(
            backup,
            error_code="contract_dependency_refresh_cleanup_failed",
            detail="lib",
        )
    attestation = _current_attestation(root)
    attestation_metadata = _write_attestation(root, attestation)
    result = {
        "schema_version": "loveengine.contract-preparation/1",
        "prepared": True,
        "toolchain": toolchain,
        "contract_source_sha256": attestation["contract_source"]["inventory_sha256"],
        "dependencies": _dependency_summary(attestation["dependencies"], installed),
        "refreshed_dependencies": [
            name for name, was_refreshed in refreshed.items() if was_refreshed
        ],
        "artifacts": attestation["artifacts"],
        "attestation": attestation_metadata,
    }
    return result
