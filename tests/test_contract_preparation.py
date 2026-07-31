from __future__ import annotations

import json
import os
import subprocess
import shutil
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

import loveengine_witness.cli_pilot as cli_pilot
import loveengine_witness.pilot_chain as pilot_chain
import loveengine_witness.pilot_runtime as pilot_runtime
import loveengine_witness.toolchain as toolchain
from loveengine_witness.cli import build_parser, dispatch
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.toolchain import (
    CONTRACT_DEPENDENCIES,
    CONTRACT_DEPENDENCY_SUBMODULES,
    DEPENDENCY_LOCK_FILENAME,
    PREPARATION_ATTESTATION_FILENAME,
    REQUIRED_CONTRACT_ARTIFACTS,
    _tree_inventory,
    prepare_contracts,
    verify_prepared_contract_artifacts,
)


def _write_contract_project(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    (contracts / "src").mkdir(parents=True)
    for name in REQUIRED_CONTRACT_ARTIFACTS:
        (contracts / "src" / f"{name}.sol").write_text(
            f"pragma solidity 0.8.26;\ncontract {name} {{}}\n",
            encoding="utf-8",
        )
    (contracts / "foundry.toml").write_text(
        """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
""",
        encoding="utf-8",
    )
    (contracts / "remappings.txt").write_text(
        "forge-std/=lib/forge-std/src/\n"
        "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/\n",
        encoding="utf-8",
    )
    return contracts


def _write_dependency(contracts: Path, name: str) -> None:
    root = contracts / "lib" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "LICENSE").write_text(name + "\n", encoding="utf-8")
    if CONTRACT_DEPENDENCY_SUBMODULES[name]:
        (root / ".gitmodules").write_bytes(
            toolchain._expected_submodule_config_bytes(name)
        )
    source = root / "src" / "Fixture.sol"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "pragma solidity 0.8.26;\ncontract Fixture {}\n",
        encoding="utf-8",
    )


def _write_dependency_lock(contracts: Path) -> None:
    entries = []
    for dependency in CONTRACT_DEPENDENCIES:
        name = dependency["name"]
        inventory = _tree_inventory(
            contracts / "lib" / name,
            error_code="fixture_error",
            detail=name,
        )
        entries.append(
            {
                "name": name,
                "package": dependency["package"],
                "commit": dependency["commit"],
                "tree_sha256": inventory["inventory_sha256"],
            }
        )
    (contracts / DEPENDENCY_LOCK_FILENAME).write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "loveengine.contract-dependency-lock/1",
                "tree_digest_algorithm": "sha256:canonical-source-tree-lf-v1",
                "dependencies": entries,
            }
        )
    )


def _write_artifacts(contracts: Path) -> None:
    for index, name in enumerate(REQUIRED_CONTRACT_ARTIFACTS, start=1):
        path = contracts / "out" / f"{name}.sol" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""{{
  "abi": [],
  "bytecode": {{"object": "0x{index:02x}"}},
  "deployedBytecode": {{"object": "0x{index + 16:02x}"}},
  "metadata": {{"settings": {{"compilationTarget": {{"src/{name}.sol": "{name}"}}}}, "sources": {{"src/{name}.sol": {{}}}}}}
}}
""",
            encoding="utf-8",
        )


def _binaries(tmp_path: Path) -> dict[str, Path]:
    binaries = {name: tmp_path / name for name in ("forge", "anvil")}
    for binary in binaries.values():
        binary.write_text("fixture", encoding="utf-8")
    return binaries


def _simulate_dependency_git_checkout(
    command: list[str], cwd: Path
) -> subprocess.CompletedProcess[str] | None:
    """Create a locked fixture tree when the controlled Git checkout completes."""

    if command[0] != "git":
        return None
    if command[1:3] == ["init", "--quiet"]:
        assert len(command) == 4
        target = Path(command[3])
        target.mkdir(parents=True)
        (target / ".git").mkdir()
    elif command[1:] == ["checkout", "--detach", "--force", "FETCH_HEAD"]:
        _write_dependency(cwd.parents[1], cwd.name)
        submodules = CONTRACT_DEPENDENCY_SUBMODULES[cwd.name]
        if submodules:
            (cwd / ".gitmodules").write_bytes(
                toolchain._expected_submodule_config_bytes(cwd.name)
            )
    elif command[1:3] == ["ls-tree", "HEAD"]:
        path = command[-1]
        submodule = next(
            item
            for item in CONTRACT_DEPENDENCY_SUBMODULES[cwd.name]
            if item["path"] == path
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"160000 commit {submodule['commit']}\t{path}\n",
        )
    elif command[1:3] == ["submodule", "update"]:
        nested = cwd / command[-1]
        nested.mkdir(parents=True)
        # Git worktrees use pointer files for initialized nested submodules.
        (nested / ".git").write_text("gitdir: ../.git/modules/fixture\n", encoding="utf-8")
    elif command[1:] == ["rev-parse", "HEAD"]:
        commit = next(
            dependency["commit"]
            for dependency in CONTRACT_DEPENDENCIES
            if dependency["name"] == cwd.name
        )
        return subprocess.CompletedProcess(command, 0, stdout=commit + "\n")
    return subprocess.CompletedProcess(command, 0, stdout="ok\n")


def _prepare_fixture(tmp_path: Path) -> Path:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )
    return contracts


def test_prepare_contracts_installs_only_missing_dependencies_and_builds_once(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    _write_dependency(contracts, "forge-std")
    _write_dependency(contracts, "openzeppelin-contracts")
    _write_dependency_lock(contracts)
    shutil.rmtree(contracts / "lib" / "openzeppelin-contracts")
    binaries = _binaries(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        git_result = _simulate_dependency_git_checkout(command, cwd)
        if git_result is not None:
            return git_result
        assert cwd == contracts
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{name} Version: 1.7.1\nCommit SHA: fixture\n",
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    result = prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert calls == [
        [str(binaries["forge"]), "--version"],
        [str(binaries["anvil"]), "--version"],
        [
            "git",
            "init",
            "--quiet",
            str(contracts / "lib" / "openzeppelin-contracts"),
        ],
        [
            "git",
            "remote",
            "add",
            "origin",
            CONTRACT_DEPENDENCIES[1]["repository_url"],
        ],
        [
            "git",
            "fetch",
            "--depth",
            "1",
            "--no-tags",
            "origin",
            CONTRACT_DEPENDENCIES[1]["commit"],
        ],
        ["git", "checkout", "--detach", "--force", "FETCH_HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "ls-tree", "HEAD", "--", "lib/forge-std"],
        ["git", "ls-tree", "HEAD", "--", "lib/erc4626-tests"],
        ["git", "ls-tree", "HEAD", "--", "lib/halmos-cheatcodes"],
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--depth",
            "1",
            "--",
            "lib/forge-std",
        ],
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--depth",
            "1",
            "--",
            "lib/erc4626-tests",
        ],
        [
            "git",
            "submodule",
            "update",
            "--init",
            "--depth",
            "1",
            "--",
            "lib/halmos-cheatcodes",
        ],
        [str(binaries["forge"]), "build", "--force", "--threads", "1"],
    ]
    assert result["schema_version"] == "loveengine.contract-preparation/1"
    assert result["prepared"] is True
    assert result["toolchain"]["forge"]["version"] == "forge Version: 1.7.1"
    assert result["dependencies"][0]["installed"] is False
    assert result["dependencies"][1]["installed"] is True
    assert result["contract_source_sha256"].startswith("sha256:")
    assert result["artifacts"]["count"] == len(REQUIRED_CONTRACT_ARTIFACTS)
    assert result["artifacts"]["inventory_sha256"].startswith("sha256:")
    assert not list((contracts / "lib" / "openzeppelin-contracts").rglob(".git"))


def test_prepare_contracts_rejects_unexpected_git_head_and_cleans_partial_tree(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    shutil.rmtree(contracts / "lib" / "openzeppelin-contracts")
    binaries = _binaries(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[0] == "git" and command[1:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="0000000000000000000000000000000000000000 raw-tool-secret\n",
            )
        git_result = _simulate_dependency_git_checkout(command, cwd)
        if git_result is not None:
            return git_result
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        pytest.fail("Forge must not build after a wrong dependency HEAD")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert "raw-tool-secret" not in error.value.message
    assert not (contracts / "lib" / "openzeppelin-contracts").exists()
    assert all(command[1:] != ["build", "--force", "--threads", "1"] for command in calls)


def test_dependency_checkout_reports_safe_git_stage_without_raw_tool_output(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1] == "fetch":
            return subprocess.CompletedProcess(
                command,
                128,
                stdout="fetch diagnostics raw-tool-secret\n",
                stderr="fatal: raw-tool-secret\n",
            )
        result = _simulate_dependency_git_checkout(command, cwd)
        if result is not None:
            return result
        pytest.fail(f"unexpected command: {command}")

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency=CONTRACT_DEPENDENCIES[0],
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert error.value.message == (
        "could not install forge-std [stage=git_fetch, exit_code=128]"
    )
    assert "raw-tool-secret" not in error.value.message
    assert not (contracts / "lib" / "forge-std").exists()


def test_dependency_checkout_rejects_unconfigured_source_before_invoking_git(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    calls: list[list[str]] = []

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency={
                "name": "forge-std",
                "package": "attacker/forge-std",
                "commit": CONTRACT_DEPENDENCIES[0]["commit"],
            },
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=lambda command, cwd: (
                calls.append(command)
                or subprocess.CompletedProcess(command, 0, stdout="unexpected\n")
            ),
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert calls == []


def test_git_checkout_environment_removes_host_git_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "GIT_EXEC_PATH",
        "git_replace_ref_base",
        "GIT_NAMESPACE",
        "GIT_ATTR_SOURCE",
        "GIT_SSL_NO_VERIFY",
        "GIT_TRACE_PACKET",
        "SSH_ASKPASS",
        "SSH_ASKPASS_REQUIRE",
    ):
        monkeypatch.setenv(key, "host-controlled")

    environment = toolchain._git_checkout_environment()
    lowered = {key.casefold(): value for key, value in environment.items()}

    for key in (
        "git_exec_path",
        "git_replace_ref_base",
        "git_namespace",
        "git_attr_source",
        "git_ssl_no_verify",
        "git_trace_packet",
        "ssh_askpass",
        "ssh_askpass_require",
    ):
        assert key not in lowered
    assert lowered["git_allow_protocol"] == "https"
    assert lowered["git_terminal_prompt"] == "0"


def test_dependency_checkout_rejects_unapproved_submodule_config_before_update(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        result = _simulate_dependency_git_checkout(command, cwd)
        if result is not None:
            if command[1:] == ["checkout", "--detach", "--force", "FETCH_HEAD"]:
                (cwd / ".gitmodules").write_text(
                    '[submodule "attacker"]\n\tpath = lib/attacker\n'
                    "\turl = https://attacker.invalid/repository.git\n",
                    encoding="utf-8",
                )
            return result
        pytest.fail(f"unexpected non-Git command: {command}")

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency=CONTRACT_DEPENDENCIES[1],
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert not any(command[1:3] == ["ls-tree", "HEAD"] for command in calls)
    assert not any(command[1:3] == ["submodule", "update"] for command in calls)
    assert not (contracts / "lib" / "openzeppelin-contracts").exists()


def test_dependency_checkout_rejects_wrong_submodule_gitlink_before_update(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["ls-tree", "HEAD"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"160000 commit {'0' * 40}\t{command[-1]}\n",
            )
        result = _simulate_dependency_git_checkout(command, cwd)
        if result is not None:
            return result
        pytest.fail(f"unexpected non-Git command: {command}")

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency=CONTRACT_DEPENDENCIES[1],
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert not any(command[1:3] == ["submodule", "update"] for command in calls)
    assert not (contracts / "lib" / "openzeppelin-contracts").exists()


def test_dependency_checkout_rejects_nested_submodule_declaration(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        result = _simulate_dependency_git_checkout(command, cwd)
        if result is not None:
            if command[1:3] == ["submodule", "update"]:
                (cwd / command[-1] / ".gitmodules").write_text(
                    '[submodule "nested"]\n\tpath = nested\n'
                    "\turl = https://attacker.invalid/repository.git\n",
                    encoding="utf-8",
                )
            return result
        pytest.fail(f"unexpected non-Git command: {command}")

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency=CONTRACT_DEPENDENCIES[1],
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert not (contracts / "lib" / "openzeppelin-contracts").exists()


def test_dependency_checkout_cleans_nested_link_without_touching_target(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    sentinel_file = sentinel / "must-survive.txt"
    sentinel_file.write_text("sentinel", encoding="utf-8")

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        result = _simulate_dependency_git_checkout(command, cwd)
        if result is not None:
            if command[1:] == ["checkout", "--detach", "--force", "FETCH_HEAD"]:
                _symlink_or_skip(cwd / "unsafe", sentinel)
            return result
        pytest.fail(f"unexpected non-Git command: {command}")

    with pytest.raises(LoveEngineError) as error:
        toolchain._install_dependency(
            cwd=contracts,
            dependency=CONTRACT_DEPENDENCIES[0],
            expected_tree_sha256="sha256:" + "0" * 64,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_install_failed"
    assert not (contracts / "lib" / "forge-std").exists()
    assert sentinel_file.read_text(encoding="utf-8") == "sentinel"


@pytest.mark.skipif(os.name != "nt", reason="exercises Windows read-only deletion")
def test_remove_owned_tree_cleans_windows_read_only_git_object(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    object_file = root / ".git" / "objects" / "object"
    object_file.parent.mkdir(parents=True)
    object_file.write_text("fixture", encoding="utf-8")
    os.chmod(object_file, stat.S_IREAD)

    toolchain._remove_owned_tree(
        root,
        error_code="fixture_cleanup_failed",
        detail="checkout",
    )

    assert not root.exists()


@pytest.mark.parametrize(
    ("binary_name", "version_output"),
    (
        ("forge", "forge Version: 1.7.0\nraw-tool-secret\n"),
        ("anvil", "wrapper output\nanvil Version: 1.7.1\nraw-tool-secret\n"),
    ),
)
def test_prepare_contracts_rejects_unpinned_or_wrapped_versions_without_output(
    tmp_path: Path,
    binary_name: str,
    version_output: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    binaries = _binaries(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        name = Path(command[0]).name
        output = (
            version_output
            if name == binary_name
            else f"{name} Version: 1.7.1\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "foundry_version_mismatch"
    assert "raw-tool-secret" not in error.value.message
    assert all("install" not in command for command in calls)
    assert all("build" not in command for command in calls)


def test_prepare_contracts_hides_failed_build_output(tmp_path: Path) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="raw stdout secret",
            stderr="raw stderr secret",
        )

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_build_failed"
    assert error.value.message == "pinned Forge build failed"
    assert "secret" not in error.value.message


def test_prepare_contracts_fails_closed_when_build_leaves_no_artifacts(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout="build claimed success")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_artifact_missing"
    assert "pilot contracts prepare" in error.value.message


@pytest.mark.parametrize(
    ("lock_bytes", "expected_code"),
    (
        (None, "contract_dependency_lock_missing"),
        (b"{}", "contract_dependency_lock_invalid"),
    ),
)
def test_prepare_contracts_rejects_missing_or_malformed_dependency_lock(
    tmp_path: Path,
    lock_bytes: bytes | None,
    expected_code: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    if lock_bytes is not None:
        (contracts / DEPENDENCY_LOCK_FILENAME).write_bytes(lock_bytes)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert command[1:] == ["--version"]
        name = Path(command[0]).name
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{name} Version: 1.7.1\n",
        )

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    (
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "../outside"
libs = ["lib"]
""",
        ),
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
remappings = ["forge-std/=../outside/"]
""",
        ),
        (
            "foundry.toml",
            """[profile.default]
src = "src"
test = "test"
script = "script"
out = "out"
libs = ["lib"]
auto_detect_remappings = true
""",
        ),
        (
            "remappings.txt",
            "forge-std/=../outside/\n"
            "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/\n",
        ),
    ),
)
def test_prepare_rejects_escaping_foundry_configuration_before_running_foundry(
    tmp_path: Path,
    relative_path: str,
    contents: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / relative_path).write_text(contents, encoding="utf-8")
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_foundry_config_invalid"
    assert calls == []


def test_prepare_rejects_unlocked_dependency_directory_before_running_foundry(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / "lib" / "evil").mkdir()
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_dependency_layout_invalid"
    assert calls == []


def test_reparse_point_fallback_is_treated_as_a_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReparsePoint:
        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return False

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(st_file_attributes=0x400)

    monkeypatch.setattr(toolchain.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    assert toolchain._is_link_or_reparse_point(ReparsePoint())


def test_safe_tree_walk_rejects_nested_reparse_before_descending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "fixture.sol").write_text("contract Fixture {}\n", encoding="utf-8")
    original = toolchain._is_link_or_reparse_point

    def is_link_or_reparse(path: Path) -> bool:
        return Path(path) == nested or original(Path(path))

    monkeypatch.setattr(toolchain, "_is_link_or_reparse_point", is_link_or_reparse)

    with pytest.raises(LoveEngineError) as error:
        toolchain._safe_tree_files(
            root,
            error_code="contract_path_symlink",
            detail="fixture",
        )

    assert error.value.code == "contract_path_symlink"


def test_safe_tree_walk_checks_skipped_git_directory_before_ignoring_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    git_metadata = root / ".git"
    git_metadata.mkdir(parents=True)
    (git_metadata / "config").write_text("[core]\n", encoding="utf-8")
    original = toolchain._is_link_or_reparse_point

    def is_link_or_reparse(path: Path) -> bool:
        return Path(path) == git_metadata or original(Path(path))

    monkeypatch.setattr(toolchain, "_is_link_or_reparse_point", is_link_or_reparse)

    with pytest.raises(LoveEngineError) as error:
        toolchain._safe_tree_files(
            root,
            error_code="contract_path_symlink",
            detail="fixture",
            skipped_names=frozenset({".git"}),
        )

    assert error.value.code == "contract_path_symlink"


def test_dependency_inventory_uses_posix_relative_path_order(tmp_path: Path) -> None:
    root = tmp_path / "dependency"
    upper = root / "README.md"
    lower = root / "foundry.toml"
    root.mkdir()
    upper.write_text("upper\n", encoding="utf-8")
    lower.write_text("lower\n", encoding="utf-8")

    inventory = toolchain._inventory(
        root,
        [lower, upper],
        error_code="fixture_error",
        detail="dependency",
    )

    assert [entry["path"] for entry in inventory["files"]] == [
        "README.md",
        "foundry.toml",
    ]


def test_prepare_accepts_lock_matching_dependency_text_without_rewriting_it(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    crlf_file = contracts / "lib" / "forge-std" / "src" / "Fixture.sol"
    crlf_file.write_bytes(b"pragma solidity 0.8.26;\r\ncontract Fixture {}\r\n")
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert crlf_file.read_bytes() == b"pragma solidity 0.8.26;\ncontract Fixture {}\n"
    assert verify_prepared_contract_artifacts(contracts)["count"] == 5


def test_prepare_accepts_lock_matching_extensionless_dependency_text(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    extensionless = contracts / "lib" / "openzeppelin-contracts" / ".gitmodules"
    extensionless.write_bytes(b"[submodule \"fixture\"]\r\npath = fixture\r\n")
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert extensionless.read_bytes() == b"[submodule \"fixture\"]\npath = fixture\n"


def test_prepare_refreshes_only_an_explicitly_requested_mismatched_dependency(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    (contracts / "lib" / "forge-std" / "LICENSE").write_text(
        "wrong tree\n",
        encoding="utf-8",
    )
    binaries = _binaries(tmp_path)
    install_cwds: list[Path] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        git_result = _simulate_dependency_git_checkout(command, cwd)
        if git_result is not None:
            if command[1:] == ["checkout", "--detach", "--force", "FETCH_HEAD"]:
                install_cwds.append(cwd.parents[1])
                (cwd / "src" / "Fixture.sol").write_bytes(
                    b"pragma solidity 0.8.26;\r\ncontract Fixture {}\r\n"
                )
            return git_result
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert cwd == contracts
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    with pytest.raises(LoveEngineError) as rejected:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )
    assert rejected.value.code == "contract_dependency_lock_mismatch"

    result = prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
        refresh_dependencies=True,
    )

    assert len(install_cwds) == 1
    assert install_cwds[0] != contracts
    assert result["refreshed_dependencies"] == ["forge-std"]
    assert (
        contracts / "lib" / "forge-std" / "src" / "Fixture.sol"
    ).read_bytes() == b"pragma solidity 0.8.26;\ncontract Fixture {}\n"


def test_prepare_preserves_refresh_backup_when_build_fails(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    original = contracts / "lib" / "forge-std" / "LICENSE"
    original.write_text("wrong tree\n", encoding="utf-8")
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        git_result = _simulate_dependency_git_checkout(command, cwd)
        if git_result is not None:
            return git_result
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert cwd == contracts
        return subprocess.CompletedProcess(command, 1, stderr="build failed")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
            refresh_dependencies=True,
        )

    assert error.value.code == "contract_build_failed"
    backups = list((contracts / "lib").glob(".loveengine-forge-std-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "LICENSE").read_text(encoding="utf-8") == "wrong tree\n"


def test_prepare_restores_backup_after_interrupted_dependency_swap(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    target = contracts / "lib" / "forge-std"
    backup = contracts / "lib" / (
        ".loveengine-forge-std-backup-" + "a" * 32
    )
    target.rename(backup)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert target.is_dir()
    assert not backup.exists()


def test_prepare_cleans_verified_backup_after_interrupted_refresh(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    backup = contracts / "lib" / (
        ".loveengine-forge-std-backup-" + "b" * 32
    )
    shutil.copytree(contracts / "lib" / "forge-std", backup)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        assert command[1:] == ["build", "--force", "--threads", "1"]
        _write_artifacts(contracts)
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    prepare_contracts(
        contracts,
        binary_lookup=binaries.__getitem__,
        command_runner=runner,
    )

    assert not backup.exists()


def test_prepare_rejects_artifact_with_unattested_compiler_source(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    binaries = _binaries(tmp_path)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            name = Path(command[0]).name
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{name} Version: 1.7.1\n"
            )
        _write_artifacts(contracts)
        artifact_path = contracts / "out" / "WitnessDAO.sol" / "WitnessDAO.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["metadata"]["sources"]["test/Injected.sol"] = {}
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="built\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_artifact_invalid"


def test_prepare_rejects_ambiguous_interrupted_refresh_backup(
    tmp_path: Path,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    source = contracts / "lib" / "forge-std"
    for suffix in ("c" * 32, "d" * 32):
        shutil.copytree(
            source,
            contracts / "lib" / f".loveengine-forge-std-backup-{suffix}",
        )
    binaries = _binaries(tmp_path)

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            contracts,
            binary_lookup=binaries.__getitem__,
            command_runner=lambda command, cwd: subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{Path(command[0]).name} Version: 1.7.1\n",
            ),
        )

    assert error.value.code == "contract_dependency_refresh_recovery_required"


def test_verify_requires_canonical_preparation_attestation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    attestation = contracts / "cache" / PREPARATION_ATTESTATION_FILENAME
    attestation.unlink()

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_missing"


def test_verify_rejects_tampered_preparation_attestation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    attestation = contracts / "cache" / PREPARATION_ATTESTATION_FILENAME
    attestation.write_bytes(attestation.read_bytes() + b"\n")

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_invalid"


def test_verify_rejects_swapped_artifact_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    target = contracts / "out" / "WitnessDAO.sol" / "WitnessDAO.json"
    source = contracts / "out" / "PublicSink.sol" / "PublicSink.json"
    target.write_bytes(source.read_bytes())

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_artifact_invalid"


def test_verify_rejects_dependency_tree_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    (contracts / "lib" / "forge-std" / "LICENSE").write_text(
        "replaced\n",
        encoding="utf-8",
    )

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_dependency_lock_mismatch"


def test_verify_rejects_dependency_lock_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    lock = contracts / DEPENDENCY_LOCK_FILENAME
    lock.write_bytes(lock.read_bytes() + b"\n")

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_mismatch"


def test_verify_rejects_source_swap_after_preparation(tmp_path: Path) -> None:
    contracts = _prepare_fixture(tmp_path)
    (contracts / "src" / "Witness.sol").write_text(
        "pragma solidity 0.8.26; contract Replaced {}\n",
        encoding="utf-8",
    )

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(contracts)

    assert error.value.code == "contract_preparation_attestation_mismatch"


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable in this test environment")


@pytest.mark.parametrize("path_kind", ("root", "ancestor", "out", "lib", "cache"))
def test_verify_rejects_contract_path_symlinks(
    tmp_path: Path,
    path_kind: str,
) -> None:
    contracts = _prepare_fixture(tmp_path)
    if path_kind == "root":
        linked = tmp_path / "contracts-link"
        _symlink_or_skip(linked, contracts)
        candidate = linked
    elif path_kind == "ancestor":
        linked = tmp_path / "parent-link"
        _symlink_or_skip(linked, tmp_path)
        candidate = linked / "contracts"
    else:
        original = contracts / path_kind
        moved = tmp_path / f"{path_kind}-target"
        original.rename(moved)
        _symlink_or_skip(original, moved)
        candidate = contracts

    with pytest.raises(LoveEngineError) as error:
        verify_prepared_contract_artifacts(candidate)

    assert error.value.code == "contract_path_symlink"


@pytest.mark.parametrize("path_kind", ("root", "ancestor", "out", "lib", "cache"))
def test_prepare_rejects_contract_path_symlinks_before_running_foundry(
    tmp_path: Path,
    path_kind: str,
) -> None:
    contracts = _write_contract_project(tmp_path)
    for dependency in CONTRACT_DEPENDENCIES:
        _write_dependency(contracts, dependency["name"])
    _write_dependency_lock(contracts)
    for directory in ("out", "cache"):
        (contracts / directory).mkdir()
    if path_kind == "root":
        linked = tmp_path / "contracts-link"
        _symlink_or_skip(linked, contracts)
        candidate = linked
    elif path_kind == "ancestor":
        linked = tmp_path / "parent-link"
        _symlink_or_skip(linked, tmp_path)
        candidate = linked / "contracts"
    else:
        original = contracts / path_kind
        moved = tmp_path / f"{path_kind}-target"
        original.rename(moved)
        _symlink_or_skip(original, moved)
        candidate = contracts
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="forge Version: 1.7.1\n")

    with pytest.raises(LoveEngineError) as error:
        prepare_contracts(
            candidate,
            binary_lookup=_binaries(tmp_path).__getitem__,
            command_runner=runner,
        )

    assert error.value.code == "contract_path_symlink"
    assert calls == []


def test_pilot_contracts_prepare_routes_to_shared_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "schema_version": "loveengine.contract-preparation/1",
        "prepared": True,
    }
    calls: list[bool] = []

    def fake_prepare(*, refresh_dependencies: bool) -> dict[str, object]:
        calls.append(refresh_dependencies)
        return expected

    monkeypatch.setattr(cli_pilot, "prepare_contracts", fake_prepare)

    args = build_parser().parse_args(["pilot", "contracts", "prepare"])

    assert dispatch(args) == expected
    refreshed = build_parser().parse_args(
        ["pilot", "contracts", "prepare", "--refresh-dependencies"]
    )
    assert dispatch(refreshed) == expected
    assert calls == [False, True]


def test_chain_initialization_requires_explicitly_prepared_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_: Path) -> dict[str, object]:
        raise LoveEngineError("contract_artifact_missing", "prepare first", 3)

    monkeypatch.setattr(pilot_chain, "verify_prepared_contract_artifacts", missing)

    with pytest.raises(LoveEngineError) as error:
        pilot_chain.initialize_chain(tmp_path, port=8545)

    assert error.value.code == "contract_artifact_missing"


def test_runtime_requires_artifacts_before_package_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> dict[str, object]:
        raise LoveEngineError("contract_artifact_missing", "prepare first", 3)

    monkeypatch.setattr(pilot_runtime, "verify_prepared_contract_artifacts", missing)
    monkeypatch.setattr(
        pilot_runtime,
        "build_package",
        lambda *args, **kwargs: pytest.fail("package build must not run"),
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_runtime.prepare_local_pilot_runtime(
            root=tmp_path,
            base_url="http://127.0.0.1:8780",
            host="127.0.0.1",
            port=8780,
            rpc_port=8545,
        )

    assert error.value.code == "contract_artifact_missing"
