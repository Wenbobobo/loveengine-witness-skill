from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import run_remote_lab  # noqa: E402
import run_core_experiments  # noqa: E402
import remote_host_preflight  # noqa: E402
import start_shared_core  # noqa: E402
import start_shared_quickstart  # noqa: E402
from loveengine_witness.toolchain import is_exact_foundry_version
from remote_host_preflight import (  # noqa: E402
    CapacityThresholds,
    SCHEMA_VERSION,
    SHARED_HOST_LEASE_SCHEMA_VERSION,
    _parse_process_snapshot,
    consume_shared_host_preflight_lease,
    create_shared_host_preflight_lease,
    evaluate_capacity,
    max_lab_cpu_assignment,
)
from run_remote_lab import (  # noqa: E402
    CORE_EVENT_COUNT,
    CORE_RECEIPT_COUNT,
    CORE_WATCHDOG_MODE,
    REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    TUNNEL_NODE_COUNT,
    _owned_group_absence_command,
    _owned_group_stop_command,
    _owned_watchdog_absence_command,
    _owned_watchdog_liveness_command,
    _no_forwarding_options,
    _remote_bash,
    _remote_listener_inspection_command,
    _ssh_options,
    _validated_queued_submission,
    _validated_core_watchdog,
    _validated_core_watchdog_result,
    _validated_quickstart_watchdog,
    _validated_quickstart_watchdog_result,
    _validated_remote_launch_lease,
    _validate_remote_core_acceptance,
    _validated_remote_listener_inspection,
    _validate_owned_remote_relative,
    _validate_remote_limits,
    _validate_remote_root,
    _validate_target,
    _validated_review_receipt,
    _wait_http_json_or_none,
    build_parser,
)
from start_shared_quickstart import (  # noqa: E402
    _parse_linux_process_start_ticks,
    _core_watchdog_command,
    _supervisor_command,
    _watchdog_command,
    select_shared_host_cpus,
    validate_shared_host_limits as validate_quickstart_limits,
    validate_core_watchdog_seconds,
    validate_watchdog_seconds,
)


WATCHDOG_SCRIPT_RELATIVE = "remote-lab/commit-001"


def test_failed_start_cleanup_refuses_unbound_or_reused_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 4321

        def wait(self, timeout: float) -> int:
            raise AssertionError(f"wait should not run for an unbound target: {timeout}")

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        start_shared_quickstart.os,
        "killpg",
        lambda pid, value: signals.append((pid, value)),
        raising=False,
    )

    unavailable = start_shared_quickstart._stop_failed_start(
        Process(),
        expected_start_ticks=None,
        expected_script=TOOLS / "start_shared_quickstart.py",
        expected_mode="--supervisor",
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_process_stat",
        lambda pid: ("S", pid, pid, 987655),
    )
    reused = start_shared_quickstart._stop_failed_start(
        Process(),
        expected_start_ticks=987654,
        expected_script=TOOLS / "start_shared_quickstart.py",
        expected_mode="--supervisor",
    )

    assert unavailable == {"status": "target_identity_unavailable", "terminated": False}
    assert reused == {"status": "target_identity_mismatch", "terminated": False}
    assert signals == []


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_failed_start_guard_rejects_supervisor_argument_smuggling() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    expected_script = (TOOLS / "start_shared_quickstart.py").resolve()
    process = subprocess.Popen(
        [
            bash,
            "-c",
            "sleep 30 & wait",
            str(expected_script),
            "--supervisor",
        ],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    try:
        assert start_shared_quickstart._owned_failed_start_state(
            process.pid,
            process_start_ticks,
            expected_script=expected_script,
            expected_mode="--supervisor",
        ) == "identity_mismatch"
    finally:
        if start_shared_quickstart._owned_session_group_has_live_members(process.pid):
            os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)


def _safe_snapshot() -> dict:
    tools = {
        name: {
            "available": True,
            "path": f"/usr/bin/{name}",
            "version": (
                "forge Version: 1.7.1"
                if name == "forge"
                else "anvil Version: 1.7.1"
                if name == "anvil"
                else f"{name} test"
            ),
        }
        for name in (
            "python3",
            "uv",
            "git",
            "bash",
            "tar",
            "ps",
            "forge",
            "anvil",
        )
    }
    return {
        "platform": "linux",
        "cpu_count": 8,
        "load_per_cpu_1m": 0.1,
        "memory_available_bytes": 8 * 1024**3,
        "disk_free_bytes": 20 * 1024**3,
        "tools": tools,
        "process_snapshot_ok": True,
        "process_snapshot_error": None,
        "relevant_processes": [],
    }


def _passing_preflight(
    workspace: Path,
    thresholds: CapacityThresholds,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace.resolve()),
        "safe_to_run": True,
        "reasons": [],
        "thresholds": {
            "max_load_per_cpu": thresholds.max_load_per_cpu,
            "min_available_memory_bytes": thresholds.min_available_memory_bytes,
            "min_free_disk_bytes": thresholds.min_free_disk_bytes,
            "min_cpu_count": thresholds.min_cpu_count,
        },
        "host": _safe_snapshot(),
        "mutated_host": False,
        "checked_at_monotonic_ns": time.monotonic_ns(),
    }


def _execution_resource_preflight(workspace: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace,
        "safe_to_run": False,
        "reasons": ["another compliant LoveEngine shared-host lab is running"],
        "thresholds": {
            "max_load_per_cpu": 0.5,
            "min_available_memory_bytes": 3 * 1024**3,
            "min_free_disk_bytes": 5 * 1024**3,
            "min_cpu_count": 2,
        },
        "host": {
            "process_snapshot_ok": False,
            "relevant_processes": [],
            "lock_held": True,
        },
        "mutated_host": False,
        "checked_at_monotonic_ns": 101,
    }


def _accepted_remote_core_report() -> dict:
    return {
        "schema_version": "loveengine.core-experiment-report/2",
        "run_id": "core-fixture-001",
        "status": "passed",
        "environment": "local_anvil",
        "actors_simulated": True,
        "resource_profile": "shared_host",
        "resource_preflight": {
            "schema_version": "loveengine.remote-host-preflight/1",
            "safe_to_run": True,
            "reasons": [],
            "mutated_host": False,
            "checked_at_monotonic_ns": 101,
            "thresholds": {
                "max_load_per_cpu": 0.5,
                "min_available_memory_bytes": 3 * 1024**3,
                "min_free_disk_bytes": 5 * 1024**3,
                "min_cpu_count": 2,
            },
            "host": {
                "cpu_count": 2,
                "reserved_cpu_count": 1,
                "max_lab_cpu_assignment": 1,
                "process_snapshot_ok": True,
                "relevant_processes": [],
            },
        },
        "resource_preflight_source": "launcher_fd_lease",
        "resource_preflight_binding": {
            "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
            "lease_id": "a" * 32,
            "issued_at_monotonic_ns": 102,
            "expires_at_monotonic_ns": 15_000_000_102,
            "launcher": {"pid": 4321, "process_start_ticks": 987654},
            "supervisor": {"pid": 4322, "process_start_ticks": 987655},
        },
        "resource_limits": {
            "nice_increment": 15,
            "process_nice": 15,
            "cpu_affinity": [0],
        },
        "contract_preparation": {
            "schema_version": "loveengine.contract-preparation/1",
            "prepared": True,
            "artifacts": {"WitnessDAO": {"bytecode_hash": "fixture"}},
        },
        "verification": {
            "offline": "offline_integrity",
            "rpc": "chain_consistency",
            "rpc_with_policy": "chain_verified",
        },
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
        "gate_ready": True,
        "recovery_tests": True,
    }


def _accepted_offline_transcript() -> dict:
    return {
        "valid": True,
        "verification_level": "offline_integrity",
        "chain_verified": False,
        "trust_bound": False,
        "run_id": "core-fixture-001",
        "environment": "local_anvil",
        "actors_simulated": True,
        "event_count": CORE_EVENT_COUNT,
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
        "gate_ready": True,
    }


def _core_acceptance_expectations() -> dict[str, object]:
    return {
        "expected_thresholds": CapacityThresholds().__dict__,
        "expected_max_cpus": 1,
    }


def test_remote_preflight_accepts_only_idle_pinned_host() -> None:
    thresholds = CapacityThresholds()
    safe, reasons = evaluate_capacity(_safe_snapshot(), thresholds)
    assert safe is True
    assert reasons == []

    busy = _safe_snapshot()
    busy["load_per_cpu_1m"] = 0.8
    busy["relevant_processes"] = [{"pid": 7, "command": "anvil"}]
    safe, reasons = evaluate_capacity(busy, thresholds)
    assert safe is False
    assert "one-minute load per CPU exceeds the shared-host limit" in reasons
    assert "existing LoveEngine/Anvil/Forge process detected" in reasons


def test_remote_preflight_rejects_missing_or_unpinned_toolchain() -> None:
    snapshot = _safe_snapshot()
    snapshot["tools"]["uv"]["available"] = False
    snapshot["tools"]["forge"]["version"] = "forge Version: 11.7.10"
    safe, reasons = evaluate_capacity(snapshot, CapacityThresholds())
    assert safe is False
    assert "required tool is missing: uv" in reasons
    assert "forge is not pinned Foundry 1.7.1" in reasons


def test_contract_preparation_accepts_only_exact_multiline_foundry_version() -> None:
    assert is_exact_foundry_version(
        "forge",
        "forge Version: 1.7.1\n"
        "Commit SHA: 4072e48705af9d93e3c0f6e29e93b5e9a40caed8\n"
    )
    assert not is_exact_foundry_version(
        "forge",
        "forge Version: 11.7.10\n"
    )
    assert not is_exact_foundry_version(
        "forge",
        "wrapper output\nforge Version: 1.7.1\n"
    )


def test_core_runner_does_not_offer_contract_prepare_bypass() -> None:
    with pytest.raises(SystemExit):
        run_core_experiments.build_parser().parse_args(["--prepare-contracts"])


def test_contract_prepare_failure_report_keeps_only_allowlisted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "raw-tool-secret-must-not-enter-report"
    completed = subprocess.CompletedProcess(
        args=["uv", "run", "loveengine", "pilot", "contracts", "prepare"],
        returncode=4,
        stdout="",
        stderr=(
            '{"error":{"code":"contract_dependency_install_failed",'
            '"message":"could not install openzeppelin-contracts '
            '[stage=git_submodule_update, exit_code=128]"}}\n'
            + secret
        ),
    )
    monkeypatch.setattr(
        run_core_experiments.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )
    experiment = run_core_experiments.Experiment(
        tmp_path,
        timeout_seconds=10,
        environment={},
    )

    with pytest.raises(RuntimeError, match="contracts-prepare failed with exit code 4"):
        experiment.run("contracts-prepare", ["uv", "run", "loveengine"])

    diagnostic = experiment.steps[0]["diagnostic"]
    assert diagnostic == {
        "kind": "contract_prepare",
        "error_code": "contract_dependency_install_failed",
        "dependency": "openzeppelin-contracts",
        "stage": "git_submodule_update",
        "command_exit_code": 128,
    }
    assert secret not in json.dumps(experiment.steps)


def test_contract_prepare_failure_rejects_unbounded_cli_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["uv"],
        returncode=4,
        stdout="",
        stderr=(
            '{"error":{"code":"contract_dependency_install_failed",'
            '"message":"could not install openzeppelin-contracts '
            '[stage=git_fetch, exit_code=128]; secret=do-not-persist"}}\n'
        ),
    )
    monkeypatch.setattr(
        run_core_experiments.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )
    experiment = run_core_experiments.Experiment(
        tmp_path,
        timeout_seconds=10,
        environment={},
    )

    with pytest.raises(RuntimeError):
        experiment.run("contracts-prepare", ["uv"])

    assert "diagnostic" not in experiment.steps[0]
    assert "do-not-persist" not in json.dumps(experiment.steps)


def test_contract_prepare_failure_uses_only_terminal_stderr_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["uv"],
        returncode=4,
        stdout=(
            '{"error":{"code":"contract_dependency_install_failed",'
            '"message":"could not install openzeppelin-contracts '
            '[stage=git_submodule_update, exit_code=128]"}}\n'
        ),
        stderr=(
            '{"error":{"code":"contract_build_failed",'
            '"message":"pinned Forge build failed"}}\n'
        ),
    )
    monkeypatch.setattr(
        run_core_experiments.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )
    experiment = run_core_experiments.Experiment(
        tmp_path,
        timeout_seconds=10,
        environment={},
    )

    with pytest.raises(RuntimeError):
        experiment.run("contracts-prepare", ["uv"])

    assert "diagnostic" not in experiment.steps[0]


def test_contract_prepare_diagnostic_requires_cli_exit_code_four(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["uv"],
        returncode=1,
        stdout="",
        stderr=(
            '{"error":{"code":"contract_dependency_install_failed",'
            '"message":"could not install openzeppelin-contracts '
            '[stage=git_submodule_update, exit_code=128]"}}\n'
        ),
    )
    monkeypatch.setattr(
        run_core_experiments.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )
    experiment = run_core_experiments.Experiment(
        tmp_path,
        timeout_seconds=10,
        environment={},
    )

    with pytest.raises(RuntimeError):
        experiment.run("contracts-prepare", ["uv"])

    assert "diagnostic" not in experiment.steps[0]


def test_contract_prepare_failure_report_and_stdout_exclude_raw_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "raw-tool-secret-must-not-enter-core-report"
    contract_failure = subprocess.CompletedProcess(
        args=["uv", "run", "loveengine", "pilot", "contracts", "prepare"],
        returncode=4,
        stdout="",
        stderr=(
            '{"error":{"code":"contract_dependency_install_failed",'
            '"message":"could not install openzeppelin-contracts '
            '[stage=git_submodule_update, exit_code=128]"}}\n'
            + secret
        ),
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["sync", "--frozen"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:] == [
            "run",
            "loveengine",
            "pilot",
            "contracts",
            "prepare",
        ]:
            return contract_failure
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(run_core_experiments.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(run_core_experiments.platform, "node", lambda: "fixture-host")
    monkeypatch.setattr(
        run_core_experiments.platform,
        "platform",
        lambda: "fixture-platform",
    )
    monkeypatch.setattr(run_core_experiments.subprocess, "run", fake_run)
    args = Namespace(
        output=tmp_path,
        events=12,
        observers=3,
        include_recovery_tests=False,
        shared_host=False,
        max_cpus=2,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        step_timeout_seconds=10,
    )

    assert run_core_experiments.run_core_experiment(args) == 1

    report_text = (tmp_path / "core-experiment-report.json").read_text(
        encoding="utf-8"
    )
    assert secret not in report_text
    assert secret not in capsys.readouterr().out
    report = json.loads(report_text)
    assert report["steps"][-1]["diagnostic"] == {
        "kind": "contract_prepare",
        "error_code": "contract_dependency_install_failed",
        "dependency": "openzeppelin-contracts",
        "stage": "git_submodule_update",
        "command_exit_code": 128,
    }


def test_remote_core_failure_message_uses_only_validated_diagnostic() -> None:
    report = {
        "status": "failed",
        "steps": [
            {
                "name": "contracts-prepare",
                "exit_code": 4,
                "diagnostic": {
                    "kind": "contract_prepare",
                    "error_code": "contract_dependency_install_failed",
                    "dependency": "openzeppelin-contracts",
                    "stage": "git_submodule_update",
                    "command_exit_code": 128,
                },
            }
        ]
    }
    assert run_remote_lab._remote_core_failure_message(report) == (
        "remote core contracts-prepare failed "
        "[dependency=openzeppelin-contracts, stage=git_submodule_update, "
        "command_exit_code=128]"
    )

    report["steps"][0]["diagnostic"]["dependency"] = "raw-tool-secret"
    assert run_remote_lab._remote_core_failure_message(report) == (
        "remote core experiment did not pass"
    )

    report["steps"][0]["diagnostic"]["dependency"] = ["openzeppelin-contracts"]
    assert run_remote_lab._remote_core_failure_message(report) == (
        "remote core experiment did not pass"
    )

    report["steps"][0]["diagnostic"]["dependency"] = "openzeppelin-contracts"
    report["steps"][0]["exit_code"] = 1
    assert run_remote_lab._remote_core_failure_message(report) == (
        "remote core experiment did not pass"
    )


def test_remote_preflight_fails_closed_when_process_inspection_fails() -> None:
    snapshot = _safe_snapshot()
    snapshot["process_snapshot_ok"] = False
    snapshot["process_snapshot_error"] = "TimeoutExpired"

    safe, reasons = evaluate_capacity(snapshot, CapacityThresholds())

    assert safe is False
    assert "could not verify existing shared-host processes" in reasons


def test_remote_preflight_detects_python_launched_loveengine(
) -> None:
    ok, _, relevant, error = _parse_process_snapshot(
        (
            "4321 1.5 0.2 "
            "python3 -m loveengine_witness.cli pilot serve --config fixture.json\n"
        )
    )

    assert ok is True
    assert error is None
    assert relevant == [
        {
            "pid": 4321,
            "command": "python3",
            "cpu_percent": 1.5,
            "memory_percent": 0.2,
        }
    ]


def test_remote_preflight_ignores_only_the_current_lab_process() -> None:
    ok, _, relevant, error = _parse_process_snapshot(
        (
            "4321 1.5 0.2 python3 tools/run_core_experiments.py\n"
            "4322 1.0 0.1 python3 tools/run_core_experiments.py\n"
        ),
        ignored_pids={4321},
    )

    assert ok is True
    assert error is None
    assert relevant == [
        {
            "pid": 4322,
            "command": "python3",
            "cpu_percent": 1.0,
            "memory_percent": 0.1,
        }
    ]


def test_remote_preflight_detects_a_uv_parent_of_the_quickstart_launcher() -> None:
    """The direct Python launcher avoids leaving this relevant parent behind."""

    ok, _, relevant, error = _parse_process_snapshot(
        "4321 0.0 0.0 uv run python tools/start_shared_quickstart.py\n",
        ignored_pids={4322},
    )

    assert ok is True
    assert error is None
    assert relevant == [
        {
            "pid": 4321,
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "command": "uv",
        }
    ]


def test_shared_host_preflight_lease_is_output_bound_one_shot_and_tamper_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "core-output"
    workspace.mkdir()
    thresholds = CapacityThresholds()
    preflight = _passing_preflight(workspace, thresholds)
    launcher_pid = os.getpid()
    launcher_start_ticks = 987654
    lease_read, lease_write = os.pipe()
    replay_read = os.dup(lease_read)

    lease_id = create_shared_host_preflight_lease(
        lease_write,
        workspace,
        thresholds,
        preflight,
        launcher_pid=launcher_pid,
        launcher_start_ticks=launcher_start_ticks,
    )
    loaded, binding = consume_shared_host_preflight_lease(
        lease_read,
        workspace,
        thresholds,
        launcher_pid=launcher_pid,
        launcher_start_ticks=launcher_start_ticks,
    )
    assert loaded == preflight
    assert binding["lease_id"] == lease_id

    with pytest.raises(ValueError, match="empty"):
        consume_shared_host_preflight_lease(
            replay_read,
            workspace,
            thresholds,
            launcher_pid=launcher_pid,
            launcher_start_ticks=launcher_start_ticks,
        )

    second_read, second_write = os.pipe()
    create_shared_host_preflight_lease(
        second_write,
        workspace,
        thresholds,
        preflight,
        launcher_pid=launcher_pid,
        launcher_start_ticks=launcher_start_ticks,
    )
    with pytest.raises(ValueError, match="launcher identity"):
        consume_shared_host_preflight_lease(
            second_read,
            workspace,
            thresholds,
            launcher_pid=launcher_pid + 1,
            launcher_start_ticks=launcher_start_ticks,
        )

    tamper_read, tamper_write = os.pipe()
    tampered = dict(preflight)
    tampered["thresholds"] = dict(preflight["thresholds"])
    tampered["thresholds"]["min_cpu_count"] = 1
    with pytest.raises(ValueError, match="non-passing"):
        create_shared_host_preflight_lease(
            tamper_write,
            workspace,
            thresholds,
            tampered,
            launcher_pid=launcher_pid,
            launcher_start_ticks=launcher_start_ticks,
        )
    os.close(tamper_read)


def test_shared_host_preflight_lease_rejects_an_unsafe_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "core-output"
    workspace.mkdir()
    thresholds = CapacityThresholds()
    preflight = _passing_preflight(workspace, thresholds)
    preflight["safe_to_run"] = False
    preflight["reasons"] = ["existing LoveEngine/Anvil/Forge process detected"]
    preflight["host"]["relevant_processes"] = [{"pid": 77, "command": "anvil"}]
    lease_read, lease_write = os.pipe()

    with pytest.raises(ValueError, match="non-passing"):
        create_shared_host_preflight_lease(
            lease_write,
            workspace,
            thresholds,
            preflight,
            launcher_pid=os.getpid(),
            launcher_start_ticks=987654,
        )
    os.close(lease_read)


def test_shared_host_preflight_lease_rejects_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "core-output"
    workspace.mkdir()
    thresholds = CapacityThresholds()
    preflight = _passing_preflight(workspace, thresholds)
    lease_read, lease_write = os.pipe()
    monotonic = iter((100, 15_000_000_101))
    monkeypatch.setattr(
        remote_host_preflight.time,
        "monotonic_ns",
        lambda: next(monotonic),
    )
    create_shared_host_preflight_lease(
        lease_write,
        workspace,
        thresholds,
        preflight,
        launcher_pid=os.getpid(),
        launcher_start_ticks=987654,
    )

    with pytest.raises(ValueError, match="expired"):
        consume_shared_host_preflight_lease(
            lease_read,
            workspace,
            thresholds,
            launcher_pid=os.getpid(),
            launcher_start_ticks=987654,
        )


def test_remote_preflight_accepts_a_process_with_empty_arguments() -> None:
    ok, top, relevant, error = _parse_process_snapshot(
        "4321 0.0 0.0\n"
    )

    assert ok is True
    assert error is None
    assert top == [
        {
            "pid": 4321,
            "command": "",
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
        }
    ]
    assert relevant == []


def test_remote_preflight_accepts_only_the_procps_dash_placeholder() -> None:
    ok, top, relevant, error = _parse_process_snapshot(
        "4321 - 0.0 [kworker]\n"
    )

    assert ok is True
    assert error is None
    assert top[0]["cpu_percent"] == 0.0
    assert relevant == []

    for value in ("nan", "-1", "unknown"):
        ok, _, _, error = _parse_process_snapshot(
            f"4321 {value} 0.0 [kworker]\n"
        )
        assert ok is False
        assert error == (
            "unparsed process rows: columns=0, pid=0, cpu=1, memory=0"
        )


@pytest.mark.parametrize(
    "thresholds",
    (
        CapacityThresholds(max_load_per_cpu=0.8),
        CapacityThresholds(min_available_memory_bytes=1),
        CapacityThresholds(min_free_disk_bytes=1),
        CapacityThresholds(min_cpu_count=1),
    ),
)
def test_remote_preflight_thresholds_cannot_be_weakened(
    thresholds: CapacityThresholds,
) -> None:
    safe, reasons = evaluate_capacity(_safe_snapshot(), thresholds)

    assert safe is False
    assert any("weakens" in reason for reason in reasons)


@pytest.mark.parametrize(
    "host,user,port",
    [
        ("host;touch-x", "daism", 22),
        ("100.120.29.82", "Daism", 22),
        ("100.120.29.82", "daism", 0),
    ],
)
def test_remote_target_rejects_shell_unsafe_values(
    host: str, user: str, port: int
) -> None:
    with pytest.raises(ValueError):
        _validate_target(host, user, port)


@pytest.mark.parametrize(
    "value",
    (
        "/srv/loveengine",
        "../loveengine",
        ".local/../loveengine",
        ".local/share/loveengine;touch-x",
    ),
)
def test_remote_root_must_stay_below_remote_home(value: str) -> None:
    with pytest.raises(ValueError):
        _validate_remote_root(value)


@pytest.mark.parametrize(
    "values",
    (
        (0.6, 3.0, 5.0, 1800),
        (0.5, 2.9, 5.0, 1800),
        (0.5, 3.0, 4.9, 1800),
        (0.5, 3.0, 5.0, 30),
        (float("nan"), 3.0, 5.0, 1800),
    ),
)
def test_remote_runner_limits_cannot_be_weakened(
    values: tuple[float, float, float, int],
) -> None:
    with pytest.raises(ValueError):
        _validate_remote_limits(
            max_load_per_cpu=values[0],
            min_memory_gib=values[1],
            min_disk_gib=values[2],
            timeout_seconds=values[3],
        )


def test_remote_ssh_is_key_only_and_has_no_password_option(tmp_path: Path) -> None:
    identity = tmp_path / "identity"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("fixture", encoding="utf-8")
    known_hosts.write_text("fixture", encoding="utf-8")
    args = Namespace(
        port=22,
        identity_file=identity,
        known_hosts=known_hosts,
    )
    options = _ssh_options(args)
    assert "BatchMode=yes" in options
    assert "PasswordAuthentication=no" in options
    assert "StrictHostKeyChecking=yes" in options
    assert "GlobalKnownHostsFile=none" in options
    assert "ControlMaster=no" in options
    assert "ForwardAgent=no" in options
    assert options[options.index("-F") + 1] in {"NUL", "/dev/null"}
    assert "password" not in build_parser().format_help().lower()
    assert _no_forwarding_options() == [
        "-o",
        "ClearAllForwardings=yes",
    ]
    assert _remote_bash("printf test").startswith("bash -c ")
    assert "bash -lc " not in _remote_bash("printf test")


def test_remote_core_acceptance_requires_report_and_transcript_boundaries() -> None:
    report = _accepted_remote_core_report()
    offline = _accepted_offline_transcript()

    accepted = _validate_remote_core_acceptance(
        report,
        offline,
        **_core_acceptance_expectations(),
    )

    assert accepted == {
        "accepted": True,
        "run_id": "core-fixture-001",
        "event_count": CORE_EVENT_COUNT,
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
        "verification_level": "offline_integrity",
    }

    report["gate_ready"] = False
    with pytest.raises(RuntimeError, match="gate_ready"):
        _validate_remote_core_acceptance(
            report,
            offline,
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    offline["trust_bound"] = True
    with pytest.raises(RuntimeError, match="trust_bound"):
        _validate_remote_core_acceptance(
            report,
            offline,
            **_core_acceptance_expectations(),
        )

    offline = _accepted_offline_transcript()
    offline["run_id"] = "other-run"
    with pytest.raises(RuntimeError, match="run IDs differ"):
        _validate_remote_core_acceptance(
            report,
            offline,
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    report["resource_limits"]["cpu_affinity"] = [0, 1]
    with pytest.raises(RuntimeError, match="enforced shared-host limits"):
        _validate_remote_core_acceptance(
            report,
            _accepted_offline_transcript(),
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    report["resource_preflight_source"] = "direct"
    with pytest.raises(RuntimeError, match="one-shot launcher lease"):
        _validate_remote_core_acceptance(
            report,
            _accepted_offline_transcript(),
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    report["resource_preflight"]["host"]["relevant_processes"] = [
        {"pid": 8, "command": "anvil"}
    ]
    with pytest.raises(RuntimeError, match="enforced shared-host limits"):
        _validate_remote_core_acceptance(
            report,
            _accepted_offline_transcript(),
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    report["resource_preflight_binding"]["expires_at_monotonic_ns"] += 1
    with pytest.raises(RuntimeError, match="invalid launcher lease binding"):
        _validate_remote_core_acceptance(
            report,
            _accepted_offline_transcript(),
            **_core_acceptance_expectations(),
        )

    report = _accepted_remote_core_report()
    with pytest.raises(RuntimeError, match="unexpected resource thresholds"):
        _validate_remote_core_acceptance(
            report,
            _accepted_offline_transcript(),
            expected_thresholds={
                **CapacityThresholds().__dict__,
                "max_load_per_cpu": 0.1,
            },
            expected_max_cpus=1,
        )


def test_remote_launch_lease_binds_the_observed_supervisor_and_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "remote-core"
    preflight = _passing_preflight(workspace, CapacityThresholds())
    start_info = {
        "output": str(workspace.resolve()),
        "nice_increment": 15,
        "process_nice": 15,
        "max_cpus": 1,
        "cpu_affinity": [0],
        "resource_preflight_source": "launcher_fd_lease",
        "resource_preflight": preflight,
        "resource_preflight_binding": {
            "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
            "lease_id": "a" * 32,
            "issued_at_monotonic_ns": preflight["checked_at_monotonic_ns"] + 1,
            "expires_at_monotonic_ns": preflight["checked_at_monotonic_ns"]
            + 15_000_000_001,
            "launcher": {"pid": 4320, "process_start_ticks": 987653},
            "supervisor": {"pid": 4321, "process_start_ticks": 987654},
        },
    }
    assert _validated_remote_launch_lease(
        start_info,
        workspace_key="output",
        supervisor_pid=4321,
        supervisor_start_ticks=987654,
        label="core",
        expected_thresholds=CapacityThresholds().__dict__,
        expected_max_cpus=1,
    )["supervisor"]["pid"] == 4321

    start_info["resource_preflight_binding"]["supervisor"]["pid"] = 4322
    with pytest.raises(RuntimeError, match="invalid FD lease binding"):
        _validated_remote_launch_lease(
            start_info,
            workspace_key="output",
            supervisor_pid=4321,
            supervisor_start_ticks=987654,
            label="core",
            expected_thresholds=CapacityThresholds().__dict__,
            expected_max_cpus=1,
        )

    start_info["resource_preflight_binding"]["supervisor"]["pid"] = 4321
    start_info["resource_preflight"]["workspace"] = str(tmp_path / "other")
    with pytest.raises(RuntimeError, match="bound passing preflight"):
        _validated_remote_launch_lease(
            start_info,
            workspace_key="output",
            supervisor_pid=4321,
            supervisor_start_ticks=987654,
            label="core",
            expected_thresholds=CapacityThresholds().__dict__,
            expected_max_cpus=1,
        )


def test_remote_listener_inspection_requires_only_owned_loopback_ports() -> None:
    inspection = {
        "schema_version": "loveengine.remote-listener-inspection/1",
        "pid": 4321,
        "process_start_ticks": 987654,
        "expected_ports": [8545, 8780],
        "available": True,
        "loopback_only": True,
        "expected_ports_listening": True,
        "owned_group_process_count": 3,
        "listener_count": 2,
        "non_loopback_listener_count": 0,
        "unexpected_listener_count": 0,
        "listeners": [
            {"address": "127.0.0.1", "family": "ipv4", "port": 8545},
            {"address": "127.0.0.1", "family": "ipv4", "port": 8780},
        ],
    }

    assert _validated_remote_listener_inspection(
        inspection,
        pid=4321,
        process_start_ticks=987654,
        expected_ports=(8780, 8545),
    ) == inspection
    assert _remote_listener_inspection_command(
        4321,
        987654,
        (8780, 8545),
        expected_script_relative="remote-lab/commit",
    ).startswith("bash -c ")
    assert "session == leader" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE
    assert "session != args.pid" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE
    assert "arguments[1] != expected_script" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE
    assert "arguments[2] != b\"--supervisor\"" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE

    inspection["loopback_only"] = False
    inspection["non_loopback_listener_count"] = 1
    with pytest.raises(RuntimeError, match="loopback-only"):
        _validated_remote_listener_inspection(
            inspection,
            pid=4321,
            process_start_ticks=987654,
            expected_ports=(8780, 8545),
        )


def test_remote_listener_inspection_binds_the_current_deployment() -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    inspection = {
        "schema_version": "loveengine.remote-listener-inspection/1",
        "pid": 4321,
        "process_start_ticks": 987654,
        "expected_ports": [8545, 8780],
        "available": True,
        "loopback_only": True,
        "expected_ports_listening": True,
        "owned_group_process_count": 1,
        "listener_count": 2,
        "non_loopback_listener_count": 0,
        "unexpected_listener_count": 0,
        "listeners": [
            {"address": "127.0.0.1", "family": "ipv4", "port": 8545},
            {"address": "127.0.0.1", "family": "ipv4", "port": 8780},
        ],
    }
    commands: list[str] = []

    def fake_ssh(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["input_text"] == run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(inspection) + "\n",
            stderr="",
        )

    lab.ssh_run = fake_ssh
    assert lab._inspect_remote_loopback_listeners(
        pid=4321,
        process_start_ticks=987654,
        expected_ports=(8780, 8545),
        expected_script_relative="remote-lab/commit",
    ) == inspection
    assert "$HOME/remote-lab/commit/tools/start_shared_quickstart.py" in commands[0]


def test_remote_quickstart_watchdog_is_bounded_and_identity_guarded() -> None:
    start_info = {
        "process_kind": "quickstart_supervisor",
        "watchdog": {
            "pid": 4322,
            "process_start_ticks": 987655,
            "timeout_seconds": REMOTE_QUICKSTART_WATCHDOG_SECONDS,
            "scope": "owned_quickstart_process_group",
            "result_path": "/tmp/pilot/.quickstart-watchdog-result.json",
        }
    }
    assert _validated_quickstart_watchdog(start_info)["pid"] == 4322
    command = _watchdog_command(
        4321,
        987654,
        REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        result_file=Path("/tmp/pilot/.quickstart-watchdog-result.json"),
        shared_host_lock_fd=12,
    )
    assert command[0]
    assert "--watchdog" in command
    assert "--watchdog-pid" in command
    assert "--watchdog-start-ticks" in command
    assert command[command.index("--shared-host-lock-fd") + 1] == "12"
    for descriptor in (-1, 0, 1, 2):
        with pytest.raises(ValueError, match="positive"):
            _watchdog_command(
                4321,
                987654,
                REMOTE_QUICKSTART_WATCHDOG_SECONDS,
                result_file=Path("/tmp/pilot/.quickstart-watchdog-result.json"),
                shared_host_lock_fd=descriptor,
            )
    supervisor = _supervisor_command(
        Path("/tmp/pilot"),
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=Path("/tmp/quickstart.log"),
        ready_file=Path("/tmp/quickstart-ready.json"),
        preflight_lease_fd=11,
        shared_host_lock_fd=12,
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    )
    assert "--supervisor" in supervisor
    assert "--ready-file" in supervisor
    assert "--shared-host-preflight-fd" in supervisor
    assert "--shared-host-lock-fd" in supervisor
    assert "--watchdog-seconds" in supervisor
    assert "loveengine" not in supervisor
    absence = _owned_watchdog_absence_command(
        4322,
        987655,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
    )
    liveness = _owned_watchdog_liveness_command(
        4322,
        987655,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
    )
    assert absence.startswith("bash -c ")
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in absence
    assert '"${#argv[@]}" -ne 13' in absence
    assert '"${argv[1]}" != "$expected_script"' in absence
    assert '"${argv[9]}" != "--watchdog-result-file"' in absence
    assert '"${argv[11]}" != "--shared-host-lock-fd"' in absence
    assert 'if [ "${argv[12]}" -lt 3 ]; then exit 9; fi' in absence
    assert "/proc/{pid}/fd/{descriptor}" in absence
    assert "fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)" in absence
    assert "expected_mode=--watchdog" in absence
    assert liveness.startswith("bash -c ")
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in liveness
    assert '"${#argv[@]}" -ne 13' in liveness
    assert '"${argv[1]}" != "$expected_script"' in liveness
    assert 'if [ "${argv[12]}" -lt 3 ]; then exit 9; fi' in liveness
    assert "/proc/{pid}/fd/{descriptor}" in liveness
    assert "fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)" in liveness
    assert "expected_mode=--watchdog" in liveness
    assert "expected_target_pid=4321" in liveness
    assert "expected_target_start=987654" in liveness
    assert f"expected_timeout={REMOTE_QUICKSTART_WATCHDOG_SECONDS}" in liveness
    assert "current_start" in liveness
    assert "current_pgrp" in liveness
    assert "current_session" in liveness
    assert 'if [ ! -e "/proc/$pid/stat" ]; then exit 0; fi' in absence
    assert "if [ ! -r \"/proc/$pid/cmdline\" ]; then " in absence
    assert "kill -" not in absence

    with pytest.raises(ValueError, match="below remote"):
        _owned_watchdog_liveness_command(
            4322,
            987655,
            target_pid=4321,
            target_process_start_ticks=987654,
            timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
            expected_script_relative="../outside",
        )

    start_info["watchdog"]["timeout_seconds"] = 901
    with pytest.raises(RuntimeError, match="not bounded"):
        _validated_quickstart_watchdog(start_info)
    for seconds in (59, 901):
        with pytest.raises(ValueError):
            validate_watchdog_seconds(seconds)


def test_remote_core_watchdog_is_bounded_and_identity_guarded() -> None:
    start_info = {
        "process_kind": "core_supervisor",
        "watchdog": {
            "pid": 4323,
            "process_start_ticks": 987656,
            "timeout_seconds": 1_800,
            "scope": "owned_core_process_group",
            "result_path": "/tmp/core-output/.core-watchdog-result.json",
        },
    }
    assert _validated_core_watchdog(start_info, timeout_seconds=1_800)["pid"] == 4323
    command = _core_watchdog_command(
        4321,
        987654,
        1_800,
        result_file=Path("/tmp/core-output/.core-watchdog-result.json"),
        shared_host_lock_fd=12,
    )
    assert "--core-watchdog" in command
    assert "--watchdog-pid" in command
    assert command[command.index("--shared-host-lock-fd") + 1] == "12"
    for descriptor in (-1, 0, 1, 2):
        with pytest.raises(ValueError, match="positive"):
            _core_watchdog_command(
                4321,
                987654,
                1_800,
                result_file=Path("/tmp/core-output/.core-watchdog-result.json"),
                shared_host_lock_fd=descriptor,
            )
    absence = _owned_watchdog_absence_command(
        4323,
        987656,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=1_800,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
        mode=CORE_WATCHDOG_MODE,
    )
    liveness = _owned_watchdog_liveness_command(
        4323,
        987656,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=1_800,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
        mode=CORE_WATCHDOG_MODE,
    )
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in absence
    assert "expected_mode=--core-watchdog" in absence
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in liveness
    assert "expected_mode=--core-watchdog" in liveness
    with pytest.raises(ValueError, match="unsupported"):
        _owned_watchdog_liveness_command(
            4323,
            987656,
            target_pid=4321,
            target_process_start_ticks=987654,
            timeout_seconds=1_800,
            expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
            mode="--other",
        )

    start_info["watchdog"]["scope"] = "owned_quickstart_process_group"
    with pytest.raises(RuntimeError, match="not bounded"):
        _validated_core_watchdog(start_info, timeout_seconds=1_800)
    for seconds in (59, 3_601):
        with pytest.raises(ValueError):
            validate_core_watchdog_seconds(seconds)

    supervisor = start_shared_core._core_supervisor_command(
        Path("/tmp/core-output"),
        log=Path("/tmp/core.log"),
        ready_file=Path("/tmp/core-ready.json"),
        preflight_lease_fd=11,
        shared_host_lock_fd=12,
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=1_800,
    )
    assert "--supervisor" in supervisor
    assert "--ready-file" in supervisor
    assert "--shared-host-preflight-fd" in supervisor
    assert "--shared-host-lock-fd" in supervisor
    assert "--watchdog-seconds" in supervisor


def test_remote_core_watchdog_terminal_result_rejects_post_liveness_failure() -> None:
    watchdog = {
        "pid": 4323,
        "process_start_ticks": 987656,
        "timeout_seconds": 1_800,
        "scope": "owned_core_process_group",
        "result_path": "/tmp/core/.core-watchdog-result.json",
    }
    failed = {
        "schema_version": "loveengine.core-watchdog-result/1",
        "guardian": {"pid": 4323, "process_start_ticks": 987656},
        "target": {"pid": 4321, "process_start_ticks": 987654},
        "timeout_seconds": 1_800,
        "status": "target_identity_mismatch",
        "terminated": False,
        "recorded_at_monotonic_ns": 123,
    }
    with pytest.raises(RuntimeError, match="did not observe normal completion"):
        _validated_core_watchdog_result(
            failed,
            watchdog=watchdog,
            target_pid=4321,
            target_process_start_ticks=987654,
            require_normal_completion=True,
        )
    passed = {**failed, "status": "target_exited_before_deadline"}
    assert _validated_core_watchdog_result(
        passed,
        watchdog=watchdog,
        target_pid=4321,
        target_process_start_ticks=987654,
        require_normal_completion=True,
    )["status"] == "target_exited_before_deadline"

    replayed_guardian = {
        **passed,
        "guardian": {"pid": 4324, "process_start_ticks": 987657},
    }
    with pytest.raises(RuntimeError, match="core watchdog terminal result is invalid"):
        _validated_core_watchdog_result(
            replayed_guardian,
            watchdog=watchdog,
            target_pid=4321,
            target_process_start_ticks=987654,
            require_normal_completion=True,
        )

    quickstart_watchdog = {
        "pid": 4322,
        "process_start_ticks": 987655,
        "timeout_seconds": REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        "scope": "owned_quickstart_process_group",
        "result_path": "/tmp/pilot/.quickstart-watchdog-result.json",
    }
    quickstart_failed = {
        **failed,
        "schema_version": "loveengine.quickstart-watchdog-result/1",
        "guardian": {"pid": 4322, "process_start_ticks": 987655},
        "timeout_seconds": REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    }
    with pytest.raises(RuntimeError, match="Quickstart watchdog did not observe"):
        _validated_quickstart_watchdog_result(
            quickstart_failed,
            watchdog=quickstart_watchdog,
            target_pid=4321,
            target_process_start_ticks=987654,
            require_normal_completion=True,
        )
    requested_teardown = {
        **quickstart_failed,
        "status": "terminated",
        "terminated": True,
    }
    assert _validated_quickstart_watchdog_result(
        requested_teardown,
        watchdog=quickstart_watchdog,
        target_pid=4321,
        target_process_start_ticks=987654,
        require_normal_completion=False,
    )["status"] == "terminated"


def test_core_supervisor_starts_guardian_before_inline_core_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    popen_kwargs: list[dict[str, object]] = []
    validated_lock_fds: list[int] = []
    supervisor_pid = os.getpid()
    launcher_pid = os.getppid()
    core_calls: list[dict[str, object]] = []

    class Watchdog:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: object) -> Watchdog:
        calls.append(command)
        popen_kwargs.append(kwargs)
        return Watchdog(4322)

    def fake_start_ticks(pid: int) -> int:
        return {
            supervisor_pid: 987654,
            launcher_pid: 987653,
            4322: 987655,
        }[pid]

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core,
        "validate_shared_host_lock_descriptor",
        validated_lock_fds.append,
    )
    monkeypatch.setattr(
        start_shared_core,
        "_core_watchdog_command",
        lambda *args, **kwargs: ["core-guardian", "--core-watchdog"],
    )
    monkeypatch.setattr(
        start_shared_core,
        "_linux_process_start_ticks",
        fake_start_ticks,
    )
    monkeypatch.setattr(start_shared_core, "_limit_process", lambda *_: ([1], 15))
    monkeypatch.setattr(start_shared_core.subprocess, "Popen", fake_popen)
    preflight = _passing_preflight(tmp_path / "core-output", CapacityThresholds())
    monkeypatch.setattr(
        start_shared_core,
        "consume_shared_host_preflight_lease",
        lambda *args, **kwargs: (
            preflight,
            {
                "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
                "lease_id": "a" * 32,
                "issued_at_monotonic_ns": 101,
                "expires_at_monotonic_ns": 102,
                "launcher": {
                    "pid": launcher_pid,
                    "process_start_ticks": 987653,
                },
            },
        ),
    )
    monkeypatch.setattr(start_shared_core, "close_shared_host_lock", lambda _: None)
    monkeypatch.setattr(
        start_shared_core,
        "run_core_experiment",
        lambda *args, **kwargs: core_calls.append(kwargs) or 0,
    )

    ready_file = tmp_path / "core-ready.json"
    result = start_shared_core.supervise_core(
        tmp_path / "core-output",
        log=tmp_path / "core.log",
        ready_file=ready_file,
        preflight_lease_fd=11,
        shared_host_lock_fd=12,
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=1800,
    )

    assert result == 0
    assert validated_lock_fds == [12]
    assert "--core-watchdog" in calls[0]
    assert len(calls) == 1
    assert popen_kwargs[0]["pass_fds"] == (12,)
    assert core_calls[0]["shared_host_preflight_source"] == "launcher_fd_lease"
    assert core_calls[0]["shared_host_limits"] == {
        "nice_increment": 15,
        "process_nice": 15,
        "cpu_affinity": [1],
    }
    launch_info = json.loads(ready_file.read_text(encoding="utf-8"))
    assert launch_info["pid"] == supervisor_pid
    assert launch_info["watchdog"]["pid"] == 4322
    assert launch_info["resource_preflight_source"] == "launcher_fd_lease"


def test_core_supervisor_preserves_start_tick_race_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor_pid = os.getpid()
    path_type = type(tmp_path)
    cleanup: list[dict[str, object]] = []

    class Watchdog:
        pid = 4322

    def start_ticks(pid: int) -> int:
        if pid == supervisor_pid:
            return 987654
        if pid == os.getppid():
            return 987653
        raise OSError("watchdog proc race")

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    # ``os.name`` is shared with pathlib on Windows. Keep this POSIX-only
    # control-flow test on the host's concrete path implementation.
    monkeypatch.setattr(start_shared_core, "Path", path_type)
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core,
        "validate_shared_host_lock_descriptor",
        lambda _: None,
    )
    monkeypatch.setattr(
        start_shared_core,
        "_core_watchdog_command",
        lambda *args, **kwargs: ["core-guardian", "--core-watchdog"],
    )
    monkeypatch.setattr(
        start_shared_core,
        "_linux_process_start_ticks",
        start_ticks,
    )
    monkeypatch.setattr(start_shared_core, "_limit_process", lambda *_: ([1], 15))
    monkeypatch.setattr(
        start_shared_core.subprocess,
        "Popen",
        lambda *args, **kwargs: Watchdog(),
    )
    monkeypatch.setattr(
        start_shared_core,
        "_stop_failed_start",
        lambda process, **kwargs: cleanup.append(
            {"pid": process.pid, **kwargs}
        ),
    )
    monkeypatch.setattr(
        start_shared_core,
        "consume_shared_host_preflight_lease",
        lambda *args, **kwargs: (
            _passing_preflight(tmp_path / "core-output", CapacityThresholds()),
            {
                "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
                "lease_id": "a" * 32,
                "issued_at_monotonic_ns": 101,
                "expires_at_monotonic_ns": 102,
                "launcher": {"pid": os.getppid(), "process_start_ticks": 987653},
            },
        ),
    )
    monkeypatch.setattr(start_shared_core, "close_shared_host_lock", lambda _: None)

    with pytest.raises(OSError, match="watchdog proc race"):
        start_shared_core.supervise_core(
            tmp_path / "core-output",
            log=tmp_path / "core.log",
            ready_file=tmp_path / "core-ready.json",
            preflight_lease_fd=11,
            shared_host_lock_fd=12,
            max_cpus=1,
            nice_increment=15,
            max_load_per_cpu=0.5,
            min_memory_gib=3.0,
            min_disk_gib=5.0,
            watchdog_seconds=1800,
        )

    assert cleanup == [
        {
            "pid": 4322,
            "expected_start_ticks": None,
            "expected_script": path_type(start_shared_core.__file__).resolve().parent
            / "start_shared_quickstart.py",
            "expected_mode": "--core-watchdog",
        }
    ]


def test_core_launcher_blocks_before_creating_owned_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The execution-time guard must run before supervisor or watchdog startup."""

    path_type = type(tmp_path)
    output = tmp_path / "core-output"
    thresholds = CapacityThresholds()
    blocked = _passing_preflight(output, thresholds)
    blocked["safe_to_run"] = False
    blocked["reasons"] = ["existing LoveEngine/Anvil/Forge process detected"]
    blocked["host"]["relevant_processes"] = [{"pid": 77, "command": "anvil"}]
    calls: list[object] = []

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    monkeypatch.setattr(start_shared_core, "Path", path_type)
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_getaffinity",
        lambda _: {0, 1},
        raising=False,
    )
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_setaffinity",
        lambda *_: None,
        raising=False,
    )
    monkeypatch.setattr(start_shared_core, "run_preflight", lambda *_: blocked)
    monkeypatch.setattr(start_shared_core, "acquire_shared_host_lock", lambda: 11)
    monkeypatch.setattr(start_shared_core, "close_shared_host_lock", lambda _: None)
    monkeypatch.setattr(
        start_shared_core.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = start_shared_core.start_core(
        output,
        log=tmp_path / "core.log",
        max_cpus=1,
        nice_increment=15,
    )

    assert result["status"] == "blocked_by_resource_guard"
    assert result["resource_preflight"]["schema_version"] == SCHEMA_VERSION
    assert result["resource_preflight"]["safe_to_run"] is False
    assert result["resource_preflight"]["mutated_host"] is False
    assert result["resource_preflight"]["thresholds"] == {
        "max_load_per_cpu": 0.5,
        "min_available_memory_bytes": 3 * 1024**3,
        "min_free_disk_bytes": 5 * 1024**3,
        "min_cpu_count": 2,
    }
    assert calls == []
    report = json.loads(
        (output / "core-experiment-report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "blocked_by_resource_guard"
    assert report["resource_preflight"] == blocked


def test_core_launcher_blocks_before_preflight_when_compliant_lab_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "core-output"
    calls: list[object] = []
    path_type = type(tmp_path)

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    monkeypatch.setattr(start_shared_core, "Path", path_type)
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_getaffinity",
        lambda _: {0, 1},
        raising=False,
    )
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_setaffinity",
        lambda *_: None,
        raising=False,
    )
    monkeypatch.setattr(start_shared_core, "acquire_shared_host_lock", lambda: None)
    monkeypatch.setattr(
        start_shared_core,
        "run_preflight",
        lambda *_: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )
    monkeypatch.setattr(
        start_shared_core.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = start_shared_core.start_core(
        output,
        log=tmp_path / "core.log",
        max_cpus=1,
        nice_increment=15,
    )

    assert result["status"] == "blocked_by_resource_guard"
    assert result["resource_preflight"]["schema_version"] == SCHEMA_VERSION
    assert result["resource_preflight"]["safe_to_run"] is False
    assert result["resource_preflight"]["mutated_host"] is False
    assert result["resource_preflight"]["thresholds"] == {
        "max_load_per_cpu": 0.5,
        "min_available_memory_bytes": 3 * 1024**3,
        "min_free_disk_bytes": 5 * 1024**3,
        "min_cpu_count": 2,
    }
    assert calls == []
    report = json.loads(
        (output / "core-experiment-report.json").read_text(encoding="utf-8")
    )
    assert report["resource_preflight_source"] == "launcher_lock"


def test_core_launcher_passes_a_one_shot_preflight_lease_to_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_type = type(tmp_path)
    output = tmp_path / "core-output"
    thresholds = CapacityThresholds()
    preflight = _passing_preflight(output, thresholds)
    calls: list[list[str]] = []
    launch_order: list[str] = []

    class Supervisor:
        pid = 4321

        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **kwargs: object) -> Supervisor:
        launch_order.append("supervisor")
        calls.append(command)
        ready_file = path_type(command[command.index("--ready-file") + 1])
        ready_file.write_text(
            json.dumps(
                {
                    "pid": 4321,
                        "process_start_ticks": 987654,
                        "process_kind": "core_supervisor",
                        "process_nice": 15,
                        "cpu_affinity": [0],
                        "resource_preflight": preflight,
                    "resource_preflight_source": "launcher_fd_lease",
                    "resource_preflight_binding": {
                        "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
                        "lease_id": "a" * 32,
                        "issued_at_monotonic_ns": 101,
                        "expires_at_monotonic_ns": 102,
                        "launcher": {"pid": 4320, "process_start_ticks": 987654},
                        "supervisor": {"pid": 4321, "process_start_ticks": 987654},
                    },
                }
            ),
            encoding="utf-8",
        )
        return Supervisor()

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    monkeypatch.setattr(start_shared_core, "Path", path_type)
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_getaffinity",
        lambda _: {0, 1},
        raising=False,
    )
    monkeypatch.setattr(
        start_shared_core.os,
        "sched_setaffinity",
        lambda *_: None,
        raising=False,
    )
    monkeypatch.setattr(start_shared_core, "run_preflight", lambda *_: preflight)
    monkeypatch.setattr(
        start_shared_core,
        "create_shared_host_preflight_lease",
        lambda descriptor, *args, **kwargs: (
            launch_order.append("lease"), os.close(descriptor), "a" * 32
        )[-1],
    )
    monkeypatch.setattr(start_shared_core, "acquire_shared_host_lock", lambda: 11)
    monkeypatch.setattr(start_shared_core, "close_shared_host_lock", lambda _: None)
    monkeypatch.setattr(
        start_shared_core,
        "_linux_process_start_ticks",
        lambda _: 987654,
    )
    monkeypatch.setattr(start_shared_core.subprocess, "Popen", fake_popen)

    result = start_shared_core.start_core(
        output,
        log=tmp_path / "core.log",
        max_cpus=1,
        nice_increment=15,
    )

    assert result["resource_preflight_source"] == "launcher_fd_lease"
    assert len(calls) == 1
    assert "--shared-host-preflight-fd" in calls[0]
    assert "--shared-host-lock-fd" in calls[0]
    assert launch_order == ["supervisor", "lease"]


def test_core_runner_does_not_expose_a_persistent_preflight_handoff() -> None:
    with pytest.raises(SystemExit):
        run_core_experiments.build_parser().parse_args(
            ["--shared-host-preflight-handoff", "fixture.json"]
        )
    with pytest.raises(SystemExit):
        start_shared_core.build_parser().parse_args(
            ["--output", "out", "--log", "log", "--shared-host-preflight-handoff", "x"]
        )
    with pytest.raises(SystemExit):
        run_core_experiments.build_parser().parse_args(["--shared-host"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory locks require fcntl")
def test_guardian_inherited_lock_blocks_a_second_compliant_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guardian retains the lock if its supervisor closes the parent FD."""

    lock_path = tmp_path / "shared-host-core.lock"
    monkeypatch.setattr(remote_host_preflight, "shared_host_lock_path", lambda: lock_path)
    lock_fd = remote_host_preflight.acquire_shared_host_lock()
    assert lock_fd is not None
    guardian = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, sys, time; os.fstat(int(sys.argv[1])); time.sleep(30)",
            str(lock_fd),
        ],
        pass_fds=(lock_fd,),
    )
    try:
        remote_host_preflight.close_shared_host_lock(lock_fd)
        lock_fd = None
        assert remote_host_preflight.acquire_shared_host_lock() is None
    finally:
        guardian.terminate()
        guardian.wait(timeout=10)
        if lock_fd is not None:
            remote_host_preflight.close_shared_host_lock(lock_fd)

    reacquired = remote_host_preflight.acquire_shared_host_lock()
    try:
        assert reacquired is not None
    finally:
        remote_host_preflight.close_shared_host_lock(reacquired)


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory locks require fcntl")
def test_shared_host_lock_descriptor_is_canonical_and_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "shared-host-core.lock"
    wrong_path = tmp_path / "not-the-shared-lock"
    monkeypatch.setattr(remote_host_preflight, "shared_host_lock_path", lambda: lock_path)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    wrong_descriptor = os.open(wrong_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        remote_host_preflight.validate_shared_host_lock_descriptor(descriptor)
        assert remote_host_preflight.acquire_shared_host_lock() is None
        for standard_descriptor in (0, 1, 2):
            with pytest.raises(ValueError, match="at least 3"):
                remote_host_preflight.validate_shared_host_lock_descriptor(
                    standard_descriptor
                )
        with pytest.raises(ValueError, match="canonical lock"):
            remote_host_preflight.validate_shared_host_lock_descriptor(
                wrong_descriptor
            )
        link_path = tmp_path / "shared-host-core-link"
        link_path.symlink_to(lock_path)
        monkeypatch.setattr(
            remote_host_preflight,
            "shared_host_lock_path",
            lambda: link_path,
        )
        with pytest.raises(ValueError, match="canonical lock"):
            remote_host_preflight.validate_shared_host_lock_descriptor(descriptor)
        monkeypatch.setattr(
            remote_host_preflight,
            "shared_host_lock_path",
            lambda: lock_path,
        )
        lock_path.unlink()
        lock_path.touch(mode=0o600)
        with pytest.raises(ValueError, match="canonical lock"):
            remote_host_preflight.validate_shared_host_lock_descriptor(descriptor)
    finally:
        os.close(wrong_descriptor)
        os.close(descriptor)

    reacquired = remote_host_preflight.acquire_shared_host_lock()
    try:
        assert reacquired is not None
    finally:
        remote_host_preflight.close_shared_host_lock(reacquired)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor allocation is required")
def test_shared_host_lock_acquisition_normalizes_a_closed_standard_descriptor(
    tmp_path: Path,
) -> None:
    pythonpath = str(TOOLS)
    if existing_pythonpath := os.environ.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + existing_pythonpath
    source = "\n".join(
        (
            "import os",
            "from remote_host_preflight import acquire_shared_host_lock, close_shared_host_lock",
            "os.close(0)",
            "try:",
            "    os.fstat(0)",
            "except OSError:",
            "    pass",
            "else:",
            "    raise SystemExit('standard descriptor remained open')",
            "descriptor = acquire_shared_host_lock()",
            "if descriptor is None or descriptor < 3:",
            "    raise SystemExit('lock descriptor was not normalized')",
            "close_shared_host_lock(descriptor)",
            "print(descriptor)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PYTHONPATH": pythonpath,
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip()) >= 3


def test_watchdog_entrypoints_validate_the_inherited_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated: list[int] = []

    def reject_lock(descriptor: int) -> None:
        validated.append(descriptor)
        raise ValueError("shared-host lock descriptor is invalid")

    monkeypatch.setattr(
        start_shared_quickstart,
        "validate_shared_host_lock_descriptor",
        reject_lock,
    )
    for mode in ("--watchdog", "--core-watchdog"):
        monkeypatch.setattr(
            start_shared_quickstart.sys,
            "argv",
            [
                "start_shared_quickstart.py",
                mode,
                "--watchdog-pid",
                "4321",
                "--watchdog-start-ticks",
                "987654",
                "--watchdog-result-file",
                str(tmp_path / f"{mode[2:]}.json"),
                "--shared-host-lock-fd",
                "12",
            ],
        )
        with pytest.raises(ValueError, match="lock descriptor is invalid"):
            start_shared_quickstart.main()
    assert validated == [12, 12]


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory locks require fcntl")
def test_watchdog_entrypoint_accepts_an_inherited_canonical_lock(
    tmp_path: Path,
) -> None:
    import fcntl

    home = tmp_path / "home"
    lock_path = (
        home
        / ".local"
        / "share"
        / "loveengine-witness-lab"
        / ".shared-host-core.lock"
    )
    lock_path.parent.mkdir(parents=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    result_path = tmp_path / "watchdog-result.json"
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "start_shared_quickstart.py"),
                "--watchdog",
                "--watchdog-pid",
                "999999",
                "--watchdog-start-ticks",
                "1",
                "--watchdog-seconds",
                "60",
                "--watchdog-result-file",
                str(result_path),
                "--shared-host-lock-fd",
                str(descriptor),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "HOME": str(home)},
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "target_exited_before_deadline"
    assert json.loads(result_path.read_text(encoding="utf-8")) == payload


def test_remote_watchdog_liveness_is_identity_bound_and_fail_closed() -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    commands: list[tuple[str, int]] = []

    def ssh_run(command: str, *, timeout: int) -> None:
        commands.append((command, timeout))

    lab.ssh_run = ssh_run
    result = lab._verify_owned_remote_watchdog_live(
        4322,
        987655,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
    )

    assert result == {
        "verified": True,
        "pid": 4322,
        "process_start_ticks": 987655,
        "target_pid": 4321,
        "target_process_start_ticks": 987654,
        "timeout_seconds": REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        "expected_script_relative": WATCHDOG_SCRIPT_RELATIVE,
        "mode": "--watchdog",
    }
    assert len(commands) == 1
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in commands[0][0]
    assert '"${#argv[@]}" -ne 13' in commands[0][0]
    assert '"${argv[1]}" != "$expected_script"' in commands[0][0]
    assert '"${argv[11]}" != "--shared-host-lock-fd"' in commands[0][0]
    assert 'if [ "${argv[12]}" -lt 3 ]; then exit 9; fi' in commands[0][0]
    assert (
        'expected_lock_path="$HOME/.local/share/loveengine-witness-lab/'
        '.shared-host-core.lock"'
        in commands[0][0]
    )
    assert "/proc/{pid}/fd/{descriptor}" in commands[0][0]
    assert "fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)" in commands[0][0]
    assert "expected_mode=--watchdog" in commands[0][0]
    assert "argv=()" in commands[0][0]
    assert "current_pgrp" in commands[0][0]
    assert "current_session" in commands[0][0]
    assert "expected_target_pid=4321" in commands[0][0]
    assert "expected_target_start=987654" in commands[0][0]
    assert (
        f"expected_timeout={REMOTE_QUICKSTART_WATCHDOG_SECONDS}"
        in commands[0][0]
    )
    assert commands[0][1] == 15

    def rejected(command: str, *, timeout: int) -> None:
        raise RuntimeError("watchdog is absent")

    lab.ssh_run = rejected
    with pytest.raises(RuntimeError, match="watchdog is absent"):
        lab._verify_owned_remote_watchdog_live(
            4322,
            987655,
            target_pid=4321,
            target_process_start_ticks=987654,
            timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
            expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
        )


def test_local_reaping_never_raises_when_terminate_or_wait_fails() -> None:
    class TerminateFailure:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise OSError("terminate failed")

    class WaitFailure:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: float) -> None:
            raise OSError("wait failed")

    terminate_result = run_remote_lab._reap_local_process(
        TerminateFailure(),
        label="node-1",
    )
    wait_result = run_remote_lab._reap_local_process(
        WaitFailure(),
        label="ssh_tunnel",
    )

    assert terminate_result["verified"] is False
    assert terminate_result["error_types"] == ["OSError"]
    assert wait_result["verified"] is False
    assert wait_result["error_types"] == ["OSError"]


def test_remote_cleanup_wrappers_report_transport_failures_without_raising() -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)

    def stop_failure(pid: int, ticks: int, **kwargs: object) -> None:
        raise OSError("SSH transport failed")

    def watchdog_failure(
        pid: int,
        ticks: int,
        *,
        target_pid: int,
        target_process_start_ticks: int,
        timeout_seconds: int,
        expected_script_relative: str,
        mode: str,
    ) -> None:
        raise subprocess.TimeoutExpired("ssh", 15)

    lab._stop_owned_remote_group = stop_failure
    lab._wait_owned_remote_watchdog_exit = watchdog_failure

    stop_result = lab._stop_owned_remote_group_safely(
        4321,
        987654,
        expected_script_relative="remote-lab/commit",
        expected_script_name="start_shared_core.py",
    )
    watchdog_result = lab._wait_owned_remote_watchdog_exit_safely(
        4322,
        987655,
        target_pid=4321,
        target_process_start_ticks=987654,
        timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
        expected_script_relative=WATCHDOG_SCRIPT_RELATIVE,
    )

    assert stop_result == {
        "verified": False,
        "stop_returncode": -1,
        "absence_returncode": -1,
        "error_type": "OSError",
    }
    assert watchdog_result == {
        "verified": False,
        "absence_returncode": -1,
        "error_type": "TimeoutExpired",
    }


def test_quickstart_supervisor_starts_guardian_before_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    popen_kwargs: list[dict[str, object]] = []
    supervisor_pid = os.getpid()
    launcher_pid = os.getppid()
    path_type = type(tmp_path)

    class CompletedChild:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def wait(self) -> int:
            return 23

    def fake_popen(command: list[str], **kwargs: object) -> CompletedChild:
        calls.append(command)
        popen_kwargs.append(kwargs)
        return CompletedChild(4322 if len(calls) == 1 else 4323)

    def fake_start_ticks(pid: int) -> int:
        return {
            supervisor_pid: 987654,
            launcher_pid: 987653,
            4322: 987655,
        }[pid]

    monkeypatch.setattr(start_shared_quickstart.os, "name", "posix")
    # ``os.name`` is shared with pathlib on Windows. Keep this POSIX-only
    # control-flow test on the host's concrete path implementation.
    monkeypatch.setattr(start_shared_quickstart, "Path", path_type)
    validated_lock_fds: list[int] = []
    monkeypatch.setattr(
        start_shared_quickstart,
        "validate_shared_host_lock_descriptor",
        validated_lock_fds.append,
    )
    monkeypatch.setattr(start_shared_quickstart, "_limit_process", lambda *_: ([1], 15))
    monkeypatch.setattr(
        start_shared_quickstart,
        "_watchdog_command",
        lambda *args, **kwargs: ["quickstart-guardian", "--watchdog"],
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_quickstart_command",
        lambda *args, **kwargs: ["quickstart-child"],
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_linux_process_start_ticks",
        fake_start_ticks,
    )
    monkeypatch.setattr(start_shared_quickstart.subprocess, "Popen", fake_popen)
    preflight = _passing_preflight(tmp_path / "pilot", CapacityThresholds())
    monkeypatch.setattr(
        start_shared_quickstart,
        "consume_shared_host_preflight_lease",
        lambda *args, **kwargs: (
            preflight,
            {
                "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
                "lease_id": "a" * 32,
                "issued_at_monotonic_ns": 101,
                "expires_at_monotonic_ns": 102,
                "launcher": {
                    "pid": launcher_pid,
                    "process_start_ticks": 987653,
                },
            },
        ),
    )
    monkeypatch.setattr(start_shared_quickstart, "close_shared_host_lock", lambda _: None)

    log_path = tmp_path / "quickstart.log"
    ready_path = tmp_path / "quickstart-ready.json"
    result = start_shared_quickstart.supervise_quickstart(
        tmp_path / "pilot",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=log_path,
        ready_file=ready_path,
        preflight_lease_fd=11,
        shared_host_lock_fd=12,
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=600,
    )

    assert result == 23
    assert validated_lock_fds == [12]
    assert calls == [
        ["quickstart-guardian", "--watchdog"],
        ["quickstart-child"],
    ]
    assert popen_kwargs[0]["pass_fds"] == (12,)
    launch_info = json.loads(ready_path.read_text(encoding="utf-8"))
    assert launch_info["pid"] == supervisor_pid
    assert launch_info["watchdog"]["pid"] == 4322
    assert launch_info["watchdog"]["result_path"].endswith(
        ".quickstart-watchdog-result.json"
    )
    assert launch_info["resource_preflight_source"] == "launcher_fd_lease"
    assert "supervisor_exits_for_guardian_cleanup" in log_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("reported_cpu_affinity", "expect_failure"),
    (([1], False), ([0], True)),
)
def test_quickstart_launcher_waits_for_supervisor_guardian_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reported_cpu_affinity: list[int],
    expect_failure: bool,
) -> None:
    path_type = type(tmp_path)
    calls: list[list[str]] = []

    class Supervisor:
        pid = 4321

    def fake_popen(command: list[str], **kwargs: object) -> Supervisor:
        calls.append(command)
        ready_file = path_type(command[command.index("--ready-file") + 1])
        root = path_type(command[command.index("--root") + 1]).resolve()
        ready_file.write_text(
            json.dumps(
                {
                    "pid": 4321,
                    "process_start_ticks": 987654,
                    "process_kind": "quickstart_supervisor",
                    "root": str(root),
                    "resource_preflight_source": "launcher_fd_lease",
                    "process_nice": 15,
                    "cpu_affinity": reported_cpu_affinity,
                    "watchdog": {
                        "pid": 4322,
                        "process_start_ticks": 987655,
                        "timeout_seconds": 600,
                        "scope": "owned_quickstart_process_group",
                        "result_path": str(
                            root / ".quickstart-watchdog-result.json"
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )
        return Supervisor()

    monkeypatch.setattr(start_shared_quickstart.os, "name", "posix")
    monkeypatch.setattr(start_shared_quickstart, "Path", path_type)
    monkeypatch.setattr(start_shared_quickstart.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_quickstart,
        "_shared_host_cpu_affinity",
        lambda _: [1],
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_linux_process_start_ticks",
        lambda _: 987654,
    )
    monkeypatch.setattr(start_shared_quickstart.subprocess, "Popen", fake_popen)
    preflight = _passing_preflight(tmp_path / "pilot", CapacityThresholds())
    monkeypatch.setattr(start_shared_quickstart, "run_preflight", lambda *_: preflight)
    monkeypatch.setattr(start_shared_quickstart, "acquire_shared_host_lock", lambda: 11)
    monkeypatch.setattr(start_shared_quickstart, "close_shared_host_lock", lambda _: None)
    monkeypatch.setattr(
        start_shared_quickstart,
        "create_shared_host_preflight_lease",
        lambda descriptor, *args, **kwargs: (
            os.close(descriptor), "a" * 32
        )[-1],
    )
    failed_cleanup: list[int] = []
    monkeypatch.setattr(
        start_shared_quickstart,
        "_stop_failed_start",
        lambda process, **kwargs: (
            failed_cleanup.append(process.pid)
            or {"status": "fixture_cleanup", "terminated": False}
        ),
    )

    invocation = lambda: start_shared_quickstart.start_quickstart(
        tmp_path / "pilot",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=tmp_path / "quickstart.log",
        max_cpus=1,
        nice_increment=15,
        watchdog_seconds=600,
    )
    if expect_failure:
        with pytest.raises(RuntimeError, match="launch identity is inconsistent"):
            invocation()
        assert failed_cleanup == [4321]
        return

    result = invocation()

    assert len(calls) == 1
    assert "--supervisor" in calls[0]
    assert "--ready-file" in calls[0]
    assert "--shared-host-preflight-fd" in calls[0]
    assert "--shared-host-lock-fd" in calls[0]
    assert result["cpu_affinity"] == [1]
    assert result["watchdog"]["pid"] == 4322


def test_watchdog_reclaims_an_orphaned_owned_session_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"status": "terminated", "terminated": True}
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_quickstart_state",
        lambda pid, ticks: "absent",
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_session_group_has_live_members",
        lambda pid: True,
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "stop_owned_quickstart_group",
        lambda pid, ticks: expected,
    )

    assert start_shared_quickstart.watch_owned_quickstart(4321, 987654, 60) == expected


def test_core_watchdog_reclaims_an_orphaned_owned_session_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"status": "terminated", "terminated": True}
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_core_state",
        lambda pid, ticks: "absent",
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_session_group_has_live_members",
        lambda pid: True,
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "stop_owned_core_group",
        lambda pid, ticks: expected,
    )

    assert start_shared_quickstart.watch_owned_core(4321, 987654, 60) == expected


def test_core_watchdog_keeps_its_guardian_alive_until_deadline_on_identity_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    probes: list[int] = []

    def state(pid: int, ticks: int) -> str:
        probes.append(pid)
        return "identity_mismatch"

    monkeypatch.setattr(start_shared_quickstart, "_owned_core_state", state)
    monkeypatch.setattr(start_shared_quickstart, "_lower_watchdog_priority", lambda: None)
    monkeypatch.setattr(start_shared_quickstart.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        start_shared_quickstart.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "stop_owned_core_group",
        lambda *_: pytest.fail("an identity-mismatched group must not be signalled"),
    )

    assert start_shared_quickstart.watch_owned_core(4321, 987654, 60) == {
        "status": "target_identity_mismatch",
        "terminated": False,
    }
    assert clock[0] == 60
    assert probes


def test_quickstart_launcher_blocks_when_another_compliant_lab_holds_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_type = type(tmp_path)
    monkeypatch.setattr(start_shared_quickstart.os, "name", "posix")
    monkeypatch.setattr(start_shared_quickstart, "Path", path_type)
    monkeypatch.setattr(start_shared_quickstart.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(start_shared_quickstart, "_shared_host_cpu_affinity", lambda _: [1])
    monkeypatch.setattr(start_shared_quickstart, "acquire_shared_host_lock", lambda: None)
    monkeypatch.setattr(
        start_shared_quickstart,
        "run_preflight",
        lambda *_: pytest.fail("preflight must not run after lock rejection"),
    )
    monkeypatch.setattr(
        start_shared_quickstart.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Quickstart must not start after lock rejection"),
    )

    result = start_shared_quickstart.start_quickstart(
        tmp_path / "pilot",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=tmp_path / "quickstart.log",
        max_cpus=1,
        nice_increment=15,
    )

    assert result["status"] == "blocked_by_resource_guard"


def test_orphan_cleanup_requires_the_original_session_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    membership = iter((True, False))
    signals: list[tuple[int, object]] = []
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_quickstart_state",
        lambda pid, ticks: "absent",
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_session_group_has_live_members",
        lambda pid: next(membership),
    )
    monkeypatch.setattr(
        start_shared_quickstart.os,
        "killpg",
        lambda pid, signal: signals.append((pid, signal)),
        raising=False,
    )
    monkeypatch.setattr(start_shared_quickstart.time, "sleep", lambda seconds: None)

    result = start_shared_quickstart.stop_owned_quickstart_group(4321, 987654)

    assert result == {"status": "terminated", "terminated": True}
    assert signals == [(4321, start_shared_quickstart.signal.SIGTERM)]


@pytest.mark.parametrize(
    ("state_name", "stop_name"),
    [
        ("_owned_quickstart_state", "stop_owned_quickstart_group"),
        ("_owned_core_state", "stop_owned_core_group"),
    ],
)
def test_orphan_cleanup_refuses_uninspectable_session(
    monkeypatch: pytest.MonkeyPatch,
    state_name: str,
    stop_name: str,
) -> None:
    signals: list[tuple[int, object]] = []
    monkeypatch.setattr(
        start_shared_quickstart,
        state_name,
        lambda pid, ticks: "absent",
    )
    monkeypatch.setattr(
        start_shared_quickstart,
        "_owned_session_group_has_live_members",
        lambda pid: None,
    )
    monkeypatch.setattr(
        start_shared_quickstart.os,
        "killpg",
        lambda pid, signal: signals.append((pid, signal)),
        raising=False,
    )

    result = getattr(start_shared_quickstart, stop_name)(4321, 987654)

    assert result == {
        "status": "process_group_inspection_unavailable",
        "terminated": False,
    }
    assert signals == []


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
@pytest.mark.parametrize("watcher_name", ["watch_owned_quickstart", "watch_owned_core"])
def test_posix_watchdog_reclaims_an_orphaned_owned_session_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watcher_name: str,
) -> None:
    """Exercise real killpg cleanup after the owned leader has already exited."""

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    child_pid_path = tmp_path / "child.pid"
    process = subprocess.Popen(
        [
            bash,
            "-c",
            'sleep 30 & printf "%s" "$!" > "$1"; sleep 0.2',
            "remote-lab-orphan",
            str(child_pid_path),
        ],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    try:
        process.wait(timeout=5)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert child_pid > 1
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )
        monkeypatch.setattr(start_shared_quickstart.os, "nice", lambda value: 0)

        watcher = getattr(start_shared_quickstart, watcher_name)
        result = watcher(
            process.pid,
            process_start_ticks,
            60,
        )

        assert result["terminated"] is True
        deadline = time.monotonic() + 4
        while (
            start_shared_quickstart._owned_session_group_has_live_members(
                process.pid
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )
    finally:
        if start_shared_quickstart._owned_session_group_has_live_members(process.pid):
            os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_posix_shell_cleanup_refuses_a_non_supervisor_orphaned_session_group(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    child_pid_path = tmp_path / "shell-child.pid"
    process = subprocess.Popen(
        [
            bash,
            "-c",
            'sleep 30 & printf "%s" "$!" > "$1"; sleep 0.2',
            "remote-lab-shell-orphan",
            str(child_pid_path),
        ],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    try:
        process.wait(timeout=5)
        assert int(child_pid_path.read_text(encoding="utf-8")) > 1
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )

        result = subprocess.run(
            _owned_group_stop_command(
                process.pid,
                process_start_ticks,
                expected_script_relative="remote-lab",
                expected_script_name="start_shared_quickstart.py",
            ),
            shell=True,
            executable=bash,
            check=False,
            timeout=10,
        )

        assert result.returncode == 9
        assert start_shared_quickstart._owned_session_group_has_live_members(process.pid)
    finally:
        if start_shared_quickstart._owned_session_group_has_live_members(process.pid):
            os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_posix_shell_cleanup_allows_an_absent_empty_session_group() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    process = subprocess.Popen(
        [bash, "-c", "sleep 0.2"],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    process.wait(timeout=5)
    assert not start_shared_quickstart._owned_session_group_has_live_members(
        process.pid
    )

    result = subprocess.run(
        _owned_group_stop_command(
            process.pid,
            process_start_ticks,
            expected_script_relative="remote-lab",
            expected_script_name="start_shared_quickstart.py",
        ),
        shell=True,
        executable=bash,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_posix_shell_cleanup_rejects_supervisor_argument_smuggling(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    script = tmp_path / "remote-lab" / "tools" / "start_shared_core.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture path only\n", encoding="utf-8")
    child_pid_path = tmp_path / "smuggled-child.pid"
    process = subprocess.Popen(
        [
            bash,
            "-c",
            'sleep 30 & printf "%s" "$!" > "$3"; wait',
            "remote-lab-smuggled-supervisor",
            str(script.resolve()),
            "--supervisor",
            str(child_pid_path),
        ],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    try:
        deadline = time.monotonic() + 2
        while not child_pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid_path.is_file()
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )
        result = subprocess.run(
            _owned_group_stop_command(
                process.pid,
                process_start_ticks,
                expected_script_relative="remote-lab",
                expected_script_name="start_shared_core.py",
            ),
            shell=True,
            executable=bash,
            check=False,
            timeout=10,
            env={**os.environ, "HOME": str(tmp_path)},
        )

        assert result.returncode == 9
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )
    finally:
        if start_shared_quickstart._owned_session_group_has_live_members(process.pid):
            os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_posix_shell_cleanup_refuses_uninspectable_orphaned_session(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the POSIX process-group regression")
    child_pid_path = tmp_path / "shell-uninspectable-child.pid"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_ps = fake_bin / "ps"
    fake_ps.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_ps.chmod(0o700)
    process = subprocess.Popen(
        [
            bash,
            "-c",
            'sleep 30 & printf "%s" "$!" > "$1"; sleep 0.2',
            "remote-lab-uninspectable-orphan",
            str(child_pid_path),
        ],
        start_new_session=True,
    )
    process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
        process.pid
    )
    try:
        process.wait(timeout=5)
        assert int(child_pid_path.read_text(encoding="utf-8")) > 1
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )

        result = subprocess.run(
            _owned_group_stop_command(
                process.pid,
                process_start_ticks,
                expected_script_relative="remote-lab",
                expected_script_name="start_shared_quickstart.py",
            ),
            shell=True,
            executable=bash,
            check=False,
            timeout=10,
            env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
        )

        assert result.returncode == 9
        assert start_shared_quickstart._owned_session_group_has_live_members(
            process.pid
        )
    finally:
        if start_shared_quickstart._owned_session_group_has_live_members(process.pid):
            os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX /proc command lines")
def test_posix_watchdog_liveness_binds_canonical_argv(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the watchdog argv regression")
    canonical = tmp_path / "remote-lab" / "tools" / "start_shared_quickstart.py"
    unexpected = tmp_path / "other" / "start_shared_quickstart.py"
    for script in (canonical, unexpected):
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
        script.chmod(0o700)
    lock_path = (
        tmp_path
        / ".local"
        / "share"
        / "loveengine-witness-lab"
        / ".shared-host-core.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    wrong_lock_descriptor = os.open(
        tmp_path / "not-the-shared-lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    import fcntl

    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def verify(script: Path, arguments: list[str]) -> int:
        process = subprocess.Popen(
            [str(script), *arguments],
            start_new_session=True,
            pass_fds=(lock_descriptor, wrong_lock_descriptor),
        )
        try:
            assert process.poll() is None
            process_start_ticks = start_shared_quickstart._linux_process_start_ticks(
                process.pid
            )
            result = subprocess.run(
                _owned_watchdog_liveness_command(
                    process.pid,
                    process_start_ticks,
                    target_pid=4321,
                    target_process_start_ticks=987654,
                    timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
                    expected_script_relative="remote-lab",
                ),
                shell=True,
                executable=bash,
                check=False,
                timeout=10,
                env={**os.environ, "HOME": str(tmp_path)},
            )
            return result.returncode
        finally:
            if process.poll() is None:
                os.killpg(process.pid, start_shared_quickstart.signal.SIGKILL)
            process.wait(timeout=5)

    correct = [
        "--watchdog",
        "--watchdog-pid",
        "4321",
        "--watchdog-start-ticks",
        "987654",
        "--watchdog-seconds",
        str(REMOTE_QUICKSTART_WATCHDOG_SECONDS),
        "--watchdog-result-file",
        str(tmp_path / "remote-lab" / ".quickstart-watchdog-result.json"),
        "--shared-host-lock-fd",
        str(lock_descriptor),
    ]
    try:
        assert verify(canonical, correct) == 0
        assert verify(canonical, [*correct[:2], "4322", *correct[3:]]) == 9
        assert verify(canonical, [*correct, "--watchdog-pid", "4321"]) == 9
        assert verify(canonical, ["--core-watchdog", *correct[1:]]) == 9
        wrong_timeout = list(correct)
        wrong_timeout[6] = "601"
        assert verify(canonical, wrong_timeout) == 9
        invalid_lock_fd = list(correct)
        invalid_lock_fd[-1] = "not-an-fd"
        assert verify(canonical, invalid_lock_fd) == 9
        standard_lock_fd = list(correct)
        standard_lock_fd[-1] = "0"
        assert verify(canonical, standard_lock_fd) == 9
        wrong_lock_fd = list(correct)
        wrong_lock_fd[-1] = str(wrong_lock_descriptor)
        assert verify(canonical, wrong_lock_fd) == 9
        assert verify(unexpected, correct) == 9
        unheld_lock_descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            unheld_lock_fd = list(correct)
            unheld_lock_fd[-1] = str(unheld_lock_descriptor)
            assert verify(canonical, unheld_lock_fd) == 9
        finally:
            os.close(unheld_lock_descriptor)
    finally:
        os.close(wrong_lock_descriptor)
        os.close(lock_descriptor)


def test_shared_host_cpu_reserve_keeps_one_cpu_unassigned() -> None:
    assert max_lab_cpu_assignment(2) == 1
    assert max_lab_cpu_assignment(3) == 2
    assert select_shared_host_cpus({0, 1}, 2) == [0]
    assert select_shared_host_cpus({0, 1, 2}, 2) == [0, 1]
    with pytest.raises(ValueError, match="reserve"):
        select_shared_host_cpus({0}, 1)
    with pytest.raises(ValueError, match="integer"):
        max_lab_cpu_assignment("2")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("max_cpus", "nice_increment"),
    [(3, 15), (2, 14), (0, 15), (2, 20)],
)
def test_shared_host_resource_limits_cannot_be_weakened(
    max_cpus: int, nice_increment: int
) -> None:
    with pytest.raises(ValueError):
        validate_quickstart_limits(max_cpus, nice_increment)
    with pytest.raises(ValueError):
        run_core_experiments.validate_shared_host_limits(
            max_cpus,
            nice_increment,
        )


def test_remote_artifacts_must_remain_in_unique_deployment() -> None:
    valid = "tmp/tunnel-pilot/release/loveengine-witness.zip"
    assert _validate_owned_remote_relative(valid) == valid

    for value in (
        "/home/daism/.ssh/authorized_keys",
        "tmp/tunnel-pilot/../other/file.zip",
        "tmp/tunnel-pilot/file.zip;touch-x",
        "../prefix-collision/file.zip",
    ):
        with pytest.raises(RuntimeError):
            _validate_owned_remote_relative(value)


def test_owned_process_cleanup_is_pid_and_deployment_bound() -> None:
    command = _owned_group_stop_command(
        4321,
        987654,
        expected_script_relative="remote-lab/commit",
        expected_script_name="start_shared_core.py",
    )
    assert command.startswith("bash -c ")
    assert not command.startswith("bash -lc ")
    assert "pid=4321" in command
    assert "expected_start=987654" in command
    assert "/proc/$pid/stat" in command
    assert "/proc/$pid/cmdline" in command
    assert "snapshot=$(ps -eo pgid=,sid=,stat=) || return 2" in command
    assert '"$pgid" = "$pid" ] && [ "$sid" = "$pid"' in command
    assert 'if [ -z "$stat" ]; then' in command
    assert 'if [ -e "/proc/$pid/stat" ]; then exit 9; fi' in command
    assert 'if group_has_live_members; then exit 9; else group_status=$?; fi' in command
    assert 'if [ "$state" = "Z" ]; then' in command
    assert 'expected_script="$expected_script_dir/start_shared_core.py"' in command
    assert '[ "${argv[1]}" != "$expected_script" ]' in command
    assert '[ "${argv[2]}" != "--supervisor" ]' in command
    assert '"$argument" = "--supervisor"' in command
    assert "group_status=$?" in command
    assert 'if [ "$group_status" -eq 1 ]; then exit 0; fi' in command
    assert 'kill -TERM -- "-$pid" 2>/dev/null || true' in command
    assert "loveengine pilot quickstart" not in command
    assert "run_core_experiments.py" not in command

    with pytest.raises(ValueError):
        _owned_group_stop_command(
            1,
            987654,
            expected_script_relative="remote-lab",
            expected_script_name="start_shared_core.py",
        )
    with pytest.raises(ValueError):
        _owned_group_stop_command(
            4321,
            0,
            expected_script_relative="remote-lab",
            expected_script_name="start_shared_core.py",
        )
    with pytest.raises(ValueError):
        _owned_group_stop_command(
            4321,
            987654,
            expected_script_relative="remote-lab",
            expected_script_name="run_core_experiments.py",
        )


def test_owned_process_absence_check_is_read_only_and_identity_guarded() -> None:
    command = _owned_group_absence_command(
        4321,
        987654,
        expected_script_relative="remote-lab/commit",
        expected_script_name="start_shared_quickstart.py",
    )
    assert command.startswith("bash -c ")
    assert "pid=4321" in command
    assert "expected_start=987654" in command
    assert "/proc/$pid/stat" in command
    assert "snapshot=$(ps -eo pgid=,sid=,stat=) || return 2" in command
    assert '"$pgid" = "$pid" ] && [ "$sid" = "$pid"' in command
    assert "2>/dev/null || true" in command
    assert 'if [ -e "/proc/$pid/stat" ]; then exit 9; fi' in command
    assert "group_status=$?" in command
    assert "kill -" not in command
    assert 'expected_script="$expected_script_dir/start_shared_quickstart.py"' in command

    with pytest.raises(ValueError):
        _owned_group_absence_command(
            1,
            987654,
            expected_script_relative="remote-lab",
            expected_script_name="start_shared_quickstart.py",
        )


def test_linux_process_start_ticks_parser_handles_spaced_command_name() -> None:
    prefix = "4321 (uv worker with spaces)"
    fields_after_command = ["S"] + [str(index) for index in range(4, 23)]
    fields_after_command[19] = "987654"
    stat = prefix + " " + " ".join(fields_after_command)

    assert _parse_linux_process_start_ticks(stat) == 987654


def test_http_wait_timeout_is_transient_only_while_tunnel_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self, returncode: int | None) -> None:
            self.returncode = returncode

        def poll(self) -> int | None:
            return self.returncode

    def timeout(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("timed out")

    monkeypatch.setattr(run_remote_lab, "_wait_http_json", timeout)
    assert (
        _wait_http_json_or_none(
            "http://127.0.0.1:1/v1/metrics",
            timeout=0.1,
            process=Process(None),  # type: ignore[arg-type]
        )
        is None
    )
    with pytest.raises(RuntimeError, match="timed out"):
        _wait_http_json_or_none(
            "http://127.0.0.1:1/v1/metrics",
            timeout=0.1,
            process=Process(1),  # type: ignore[arg-type]
        )


def test_tunnel_review_receipt_must_bind_each_of_three_nodes() -> None:
    assert TUNNEL_NODE_COUNT == 3
    node = "0x" + "1" * 40
    result = {
        "node": node,
        "rejected": 0,
        "receipts": [
            {
                "node": node,
                "task_id": "task-2",
                "status": "completed",
                "result": {
                    "dispute_id": "dispute-2",
                    "evidence_verified": True,
                },
            }
        ],
    }

    assert (
        _validated_review_receipt(
            result,
            task_id="task-2",
            dispute_id="dispute-2",
            expected_node=node,
        )["status"]
        == "completed"
    )
    result["receipts"][0]["result"]["evidence_verified"] = False
    with pytest.raises(RuntimeError, match="evidence-verified"):
        _validated_review_receipt(
            result,
            task_id="task-2",
            dispute_id="dispute-2",
            expected_node=node,
        )


def test_tunnel_task_submission_must_bind_the_queued_recipient() -> None:
    node = "0x" + "2" * 40
    submission = {
        "queued": True,
        "task_id": "task-2",
        "recipient": node,
        "task": {
            "task_id": "task-2",
            "task_type": "review_dispute",
            "recipient": node,
        },
    }
    assert _validated_queued_submission(
        submission,
        task_id="task-2",
        recipient=node,
    )["recipient"] == node

    submission["recipient"] = "0x" + "3" * 40
    with pytest.raises(RuntimeError, match="expected recipient"):
        _validated_queued_submission(
            submission,
            task_id="task-2",
            recipient=node,
        )


def test_remote_runner_writes_failure_phase_and_postflight_cleanup(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(host="test-host", output=tmp_path)
    lab.current_phase = "remote_core"
    lab.current_output = tmp_path
    lab.current_commit = "a" * 40
    lab.last_preflight = {"safe_to_run": True}

    def fail() -> dict:
        raise RuntimeError("bounded fixture failure")

    lab._run_once = fail
    lab.preflight = lambda: {
        "safe_to_run": False,
        "host": {
            "process_snapshot_ok": True,
            "relevant_processes": [],
        },
    }

    report = lab.run()

    assert report["status"] == "failed"
    assert report["phase"] == "remote_core"
    assert report["error"]["type"] == "RuntimeError"
    assert report["postflight_cleanup_verified"] is True
    assert (
        tmp_path / "remote-lab-report.json"
    ).read_text(encoding="utf-8").find('"status": "failed"') >= 0


def test_remote_runner_surfaces_owned_core_resource_block(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    execution_preflight = _execution_resource_preflight(
        "/home/daism/remote-lab/commit/tmp/remote-core"
    )
    lab.ssh_run = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[],
        returncode=4,
        stdout=json.dumps(
            {
                "status": "blocked_by_resource_guard",
                "resource_preflight": execution_preflight,
            }
        )
        + "\n",
        stderr="",
    )
    lab._recover_owned_remote_core_start = lambda **kwargs: pytest.fail(
        "a structured resource rejection must not enter startup recovery"
    )

    with pytest.raises(run_remote_lab.ResourceGuardBlocked, match="core blocked"):
        lab._run_owned_remote_core(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
            max_cpus=1,
        )

    assert lab.execution_resource_blocks == [
        {"phase": "core", "preflight": execution_preflight}
    ]


def test_remote_runner_preserves_quickstart_execution_resource_block(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    lab.preflight = lambda: {"safe_to_run": True}
    lab._lab_cpu_cap = lambda _: 1
    lab._remote_free_ports = lambda: (8780, 8545)
    lab._recover_owned_remote_quickstart_start = lambda **kwargs: pytest.fail(
        "a structured resource rejection must not enter startup recovery"
    )
    lab._download_remote_quickstart_diagnostics = lambda **kwargs: pytest.fail(
        "a structured resource rejection must not download startup diagnostics"
    )
    execution_preflight = _execution_resource_preflight(
        "/home/daism/remote-lab/commit/tmp/tunnel-pilot"
    )
    lab.ssh_run = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[],
        returncode=4,
        stdout=json.dumps(
            {
                "status": "blocked_by_resource_guard",
                "resource_preflight": execution_preflight,
            }
        )
        + "\n",
        stderr="",
    )

    with pytest.raises(run_remote_lab.ResourceGuardBlocked, match="quickstart blocked"):
        lab._tunnel_smoke(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
        )

    assert lab.execution_resource_blocks == [
        {"phase": "quickstart", "preflight": execution_preflight}
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "loveengine.remote-host-preflight/0"),
        ("safe_to_run", True),
        ("reasons", []),
        ("workspace", "/home/daism/unbound/tmp/remote-core"),
        ("thresholds", {}),
    ],
)
def test_execution_resource_block_rejects_unbound_or_incomplete_preflight(
    field: str,
    value: object,
) -> None:
    preflight = _execution_resource_preflight(
        "/home/daism/remote-lab/commit/tmp/remote-core"
    )
    preflight[field] = value

    with pytest.raises(RuntimeError, match="invalid resource rejection"):
        run_remote_lab._validated_execution_resource_block(
            {
                "status": "blocked_by_resource_guard",
                "resource_preflight": preflight,
            },
            label="core",
            expected_thresholds={
                "max_load_per_cpu": 0.5,
                "min_available_memory_bytes": 3 * 1024**3,
                "min_free_disk_bytes": 5 * 1024**3,
                "min_cpu_count": 2,
            },
            expected_workspace_relative="remote-lab/commit/tmp/remote-core",
        )


def test_remote_runner_rejects_unbound_core_execution_resource_block(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    preflight = _execution_resource_preflight(
        "/home/daism/remote-lab/commit/tmp/remote-core"
    )
    preflight["safe_to_run"] = True
    lab.ssh_run = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[],
        returncode=4,
        stdout=json.dumps(
            {
                "status": "blocked_by_resource_guard",
                "resource_preflight": preflight,
            }
        )
        + "\n",
        stderr="",
    )

    with pytest.raises(RuntimeError, match="invalid resource rejection"):
        lab._run_owned_remote_core(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
            max_cpus=1,
        )


def test_remote_runner_rejects_unbound_quickstart_execution_resource_block(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    lab.preflight = lambda: {"safe_to_run": True}
    lab._lab_cpu_cap = lambda _: 1
    lab._remote_free_ports = lambda: (8780, 8545)
    preflight = _execution_resource_preflight(
        "/home/daism/remote-lab/commit/tmp/tunnel-pilot"
    )
    preflight["reasons"] = []
    lab.ssh_run = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[],
        returncode=4,
        stdout=json.dumps(
            {
                "status": "blocked_by_resource_guard",
                "resource_preflight": preflight,
            }
        )
        + "\n",
        stderr="",
    )

    with pytest.raises(RuntimeError, match="invalid resource rejection"):
        lab._tunnel_smoke(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
        )


def test_remote_runner_maps_execution_resource_block_to_blocked_report(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(host="test-host", output=tmp_path)
    lab.current_phase = "tunnel_smoke"
    lab.current_output = tmp_path
    lab.current_commit = "a" * 40
    lab.last_preflight = {"safe_to_run": True}
    execution_preflight = {
        "schema_version": SCHEMA_VERSION,
        "safe_to_run": False,
        "reasons": ["shared host became busy"],
    }
    lab.execution_resource_blocks = [
        {"phase": "quickstart", "preflight": execution_preflight}
    ]

    def blocked() -> dict:
        raise run_remote_lab.ResourceGuardBlocked(
            "quickstart",
            execution_preflight,
        )

    lab._run_once = blocked
    lab.preflight = lambda: {
        "safe_to_run": False,
        "host": {"process_snapshot_ok": False, "relevant_processes": []},
    }

    report = lab.run()

    assert report["status"] == "blocked_by_resource_guard"
    assert report["resource_guard"] == {
        "phase": "quickstart",
        "preflight": execution_preflight,
    }
    assert report["execution_resource_blocks"] == lab.execution_resource_blocks


def test_remote_runner_recovers_owned_identity_after_core_start_timeout(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    lab.downloaded_diagnostics = []
    cleanup_calls: list[tuple[str, int]] = []

    def timeout_start(command: str, *, timeout: int, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("ssh", timeout)

    def copy_from_remote(remote: str, local: Path) -> None:
        if remote.endswith(".core-launch.json"):
            local.write_text(
                json.dumps(
                    {
                        "pid": 4321,
                        "process_start_ticks": 987654,
                        "process_kind": "core_supervisor",
                        "output": "/home/daism/remote-lab/commit/tmp/remote-core",
                        "nice_increment": 15,
                        "process_nice": 15,
                        "max_cpus": 1,
                        "cpu_affinity": [0],
                        "resource_preflight_source": "launcher_fd_lease",
                        "resource_preflight": {
                            "schema_version": SCHEMA_VERSION,
                            "workspace": "/home/daism/remote-lab/commit/tmp/remote-core",
                            "safe_to_run": True,
                            "reasons": [],
                            "thresholds": CapacityThresholds().__dict__,
                            "host": _safe_snapshot(),
                            "mutated_host": False,
                            "checked_at_monotonic_ns": 100,
                        },
                        "resource_preflight_binding": {
                            "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
                            "lease_id": "a" * 32,
                            "issued_at_monotonic_ns": 101,
                            "expires_at_monotonic_ns": 15_000_000_101,
                            "launcher": {"pid": 4320, "process_start_ticks": 987653},
                            "supervisor": {"pid": 4321, "process_start_ticks": 987654},
                        },
                        "watchdog": {
                            "pid": 4322,
                            "process_start_ticks": 987655,
                            "timeout_seconds": 1800,
                            "scope": "owned_core_process_group",
                            "result_path": "/tmp/remote/.core-watchdog-result.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            return
        raise RuntimeError("diagnostic not present")

    lab.ssh_run = timeout_start
    lab._copy_from_remote = copy_from_remote
    lab._stop_owned_remote_group_safely = lambda pid, ticks, **kwargs: (
        cleanup_calls.append(("group", pid))
        or {"verified": True}
    )
    lab._wait_owned_remote_watchdog_exit_safely = lambda pid, ticks, **kwargs: (
        cleanup_calls.append(("watchdog", pid))
        or {"verified": True}
    )

    with pytest.raises(RuntimeError, match="startup failed after verified owned cleanup"):
        lab._run_owned_remote_core(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
            max_cpus=1,
        )

    assert cleanup_calls == [("group", 4321), ("watchdog", 4322)]
    assert "core-launch.json" in lab.downloaded_diagnostics


def test_remote_runner_recovers_quickstart_identity_after_start_timeout(
    tmp_path: Path,
) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        timeout_seconds=1800,
    )
    lab.downloaded_diagnostics = []
    lab.preflight = lambda: {"safe_to_run": True}
    lab._lab_cpu_cap = lambda _: 1
    lab._remote_free_ports = lambda: (8780, 8545)
    commands: list[str] = []

    def timeout_start(command: str, **kwargs: object) -> None:
        commands.append(command)
        raise subprocess.TimeoutExpired("ssh", 30)

    lab.ssh_run = timeout_start
    lab._recover_owned_remote_quickstart_start = lambda **kwargs: {
        "identity_recovered": True,
        "group_cleanup": {"verified": True},
        "watchdog_cleanup": {"verified": True},
    }
    lab._download_remote_quickstart_diagnostics = lambda **kwargs: None

    with pytest.raises(
        RuntimeError,
        match="Quickstart startup failed after verified owned cleanup",
    ):
        lab._tunnel_smoke(
            deployment_rel="remote-lab/commit",
            remote_deployment="$HOME/remote-lab/commit",
            local_output=tmp_path,
        )

    assert lab.start_recoveries == [
        {
            "phase": "quickstart",
            "result": {
                "identity_recovered": True,
                "group_cleanup": {"verified": True},
                "watchdog_cleanup": {"verified": True},
            },
        }
    ]
    assert "exec python3 tools/start_shared_quickstart.py" in commands[0]
    assert "exec uv run python tools/start_shared_quickstart.py" not in commands[0]


def test_remote_runner_writes_capacity_block_report(tmp_path: Path) -> None:
    lab = object.__new__(run_remote_lab.RemoteLab)
    output = tmp_path / "blocked-report"
    lab.args = Namespace(host="test-host", output=output)
    lab.current_phase = "preflight"
    lab.current_output = None
    lab.current_commit = None
    lab.last_preflight = None
    lab._run_once = lambda: {
        "schema_version": "loveengine.remote-lab-report/1",
        "status": "blocked",
        "target": "test-host",
        "preflight": {"safe_to_run": False},
    }

    report = lab.run()

    assert report["status"] == "blocked"
    assert report["phase"] == "preflight"
    saved = output / "remote-lab-report.json"
    assert '"status": "blocked"' in saved.read_text(encoding="utf-8")


def test_remote_runner_preserves_an_existing_requested_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = tmp_path / "existing-output"
    requested.mkdir()
    sentinel = requested / "remote-lab-report.json"
    sentinel.write_text("preserve-me\n", encoding="utf-8")
    monkeypatch.setattr(run_remote_lab, "ROOT", tmp_path / "repo")
    lab = object.__new__(run_remote_lab.RemoteLab)
    lab.args = Namespace(host="test-host", output=requested)
    lab.current_output = None

    report = {"status": "blocked"}
    lab._write_report(report)

    assert sentinel.read_text(encoding="utf-8") == "preserve-me\n"
    assert report["requested_output_collision"] == {
        "path": str(requested.resolve()),
        "preserved": True,
    }
    assert lab.current_output is not None
    assert lab.current_output != requested
    assert (lab.current_output / "remote-lab-report.json").is_file()
