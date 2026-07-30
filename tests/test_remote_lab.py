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
import start_shared_core  # noqa: E402
import start_shared_quickstart  # noqa: E402
from loveengine_witness.toolchain import is_exact_foundry_version
from remote_host_preflight import (  # noqa: E402
    CapacityThresholds,
    _parse_process_snapshot,
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
    _validated_quickstart_watchdog,
    _validate_remote_core_acceptance,
    _validated_remote_listener_inspection,
    _validate_remote_artifact,
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
            "mutated_host": False,
            "host": {
                "cpu_count": 2,
                "reserved_cpu_count": 1,
                "max_lab_cpu_assignment": 1,
            },
        },
        "resource_limits": {
            "nice_increment": 15,
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

    accepted = _validate_remote_core_acceptance(report, offline)

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
        _validate_remote_core_acceptance(report, offline)

    report = _accepted_remote_core_report()
    offline["trust_bound"] = True
    with pytest.raises(RuntimeError, match="trust_bound"):
        _validate_remote_core_acceptance(report, offline)

    offline = _accepted_offline_transcript()
    offline["run_id"] = "other-run"
    with pytest.raises(RuntimeError, match="run IDs differ"):
        _validate_remote_core_acceptance(report, offline)

    report = _accepted_remote_core_report()
    report["resource_limits"]["cpu_affinity"] = [0, 1]
    with pytest.raises(RuntimeError, match="enforced shared-host limits"):
        _validate_remote_core_acceptance(report, _accepted_offline_transcript())


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
        4321, 987654, (8780, 8545)
    ).startswith("bash -c ")
    assert "session == leader" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE
    assert "session != args.pid" in run_remote_lab.REMOTE_LISTENER_INSPECTION_SOURCE

    inspection["loopback_only"] = False
    inspection["non_loopback_listener_count"] = 1
    with pytest.raises(RuntimeError, match="loopback-only"):
        _validated_remote_listener_inspection(
            inspection,
            pid=4321,
            process_start_ticks=987654,
            expected_ports=(8780, 8545),
        )


def test_remote_quickstart_watchdog_is_bounded_and_identity_guarded() -> None:
    start_info = {
        "process_kind": "quickstart_supervisor",
        "watchdog": {
            "pid": 4322,
            "process_start_ticks": 987655,
            "timeout_seconds": REMOTE_QUICKSTART_WATCHDOG_SECONDS,
            "scope": "owned_quickstart_process_group",
        }
    }
    assert _validated_quickstart_watchdog(start_info)["pid"] == 4322
    command = _watchdog_command(
        4321,
        987654,
        REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    )
    assert command[0]
    assert "--watchdog" in command
    assert "--watchdog-pid" in command
    assert "--watchdog-start-ticks" in command
    supervisor = _supervisor_command(
        Path("/tmp/pilot"),
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=Path("/tmp/quickstart.log"),
        ready_file=Path("/tmp/quickstart-ready.json"),
        max_cpus=1,
        nice_increment=15,
        watchdog_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    )
    assert "--supervisor" in supervisor
    assert "--ready-file" in supervisor
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
    assert '"$expected_script")' in absence
    assert "expected_mode=--watchdog" in absence
    assert liveness.startswith("bash -c ")
    assert f"expected_script_relative={WATCHDOG_SCRIPT_RELATIVE}" in liveness
    assert '"$expected_script")' in liveness
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
        },
    }
    assert _validated_core_watchdog(start_info, timeout_seconds=1_800)["pid"] == 4323
    command = _core_watchdog_command(4321, 987654, 1_800)
    assert "--core-watchdog" in command
    assert "--watchdog-pid" in command
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
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=1_800,
    )
    assert "--supervisor" in supervisor
    assert "--ready-file" in supervisor
    assert "--watchdog-seconds" in supervisor


def test_core_supervisor_starts_guardian_before_core_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    supervisor_pid = os.getpid()

    class Child:
        def __init__(self, pid: int, result: int = 0) -> None:
            self.pid = pid
            self.result = result

        def wait(self) -> int:
            return self.result

    def fake_popen(command: list[str], **kwargs: object) -> Child:
        calls.append(command)
        return Child(4322 if len(calls) == 1 else 4323)

    def fake_start_ticks(pid: int) -> int:
        return {
            supervisor_pid: 987654,
            4322: 987655,
        }[pid]

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core,
        "_core_watchdog_command",
        lambda *args: ["core-guardian", "--core-watchdog"],
    )
    monkeypatch.setattr(
        start_shared_core,
        "_linux_process_start_ticks",
        fake_start_ticks,
    )
    monkeypatch.setattr(start_shared_core.subprocess, "Popen", fake_popen)

    ready_file = tmp_path / "core-ready.json"
    result = start_shared_core.supervise_core(
        tmp_path / "core-output",
        log=tmp_path / "core.log",
        ready_file=ready_file,
        max_cpus=1,
        nice_increment=15,
        max_load_per_cpu=0.5,
        min_memory_gib=3.0,
        min_disk_gib=5.0,
        watchdog_seconds=1800,
    )

    assert result == 0
    assert "--core-watchdog" in calls[0]
    assert "run_core_experiments.py" in calls[1][1]
    launch_info = json.loads(ready_file.read_text(encoding="utf-8"))
    assert launch_info["pid"] == supervisor_pid
    assert launch_info["watchdog"]["pid"] == 4322


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
        raise OSError("watchdog proc race")

    monkeypatch.setattr(start_shared_core.os, "name", "posix")
    # ``os.name`` is shared with pathlib on Windows. Keep this POSIX-only
    # control-flow test on the host's concrete path implementation.
    monkeypatch.setattr(start_shared_core, "Path", path_type)
    monkeypatch.setattr(start_shared_core.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        start_shared_core,
        "_core_watchdog_command",
        lambda *args: ["core-guardian", "--core-watchdog"],
    )
    monkeypatch.setattr(
        start_shared_core,
        "_linux_process_start_ticks",
        start_ticks,
    )
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

    with pytest.raises(OSError, match="watchdog proc race"):
        start_shared_core.supervise_core(
            tmp_path / "core-output",
            log=tmp_path / "core.log",
            ready_file=tmp_path / "core-ready.json",
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
    assert '"$expected_script")' in commands[0][0]
    assert "expected_mode=--watchdog" in commands[0][0]
    assert "argv=()" in commands[0][0]
    assert "target_pid_count" in commands[0][0]
    assert "target_start_count" in commands[0][0]
    assert "timeout_count" in commands[0][0]
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

    def stop_failure(pid: int, ticks: int) -> None:
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

    stop_result = lab._stop_owned_remote_group_safely(4321, 987654)
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
    supervisor_pid = os.getpid()
    path_type = type(tmp_path)

    class CompletedChild:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def wait(self) -> int:
            return 23

    def fake_popen(command: list[str], **kwargs: object) -> CompletedChild:
        calls.append(command)
        return CompletedChild(4322 if len(calls) == 1 else 4323)

    def fake_start_ticks(pid: int) -> int:
        return {
            supervisor_pid: 987654,
            4322: 987655,
        }[pid]

    monkeypatch.setattr(start_shared_quickstart.os, "name", "posix")
    # ``os.name`` is shared with pathlib on Windows. Keep this POSIX-only
    # control-flow test on the host's concrete path implementation.
    monkeypatch.setattr(start_shared_quickstart, "Path", path_type)
    monkeypatch.setattr(start_shared_quickstart, "_limit_process", lambda *_: None)
    monkeypatch.setattr(
        start_shared_quickstart,
        "_watchdog_command",
        lambda *args: ["quickstart-guardian", "--watchdog"],
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

    log_path = tmp_path / "quickstart.log"
    ready_path = tmp_path / "quickstart-ready.json"
    result = start_shared_quickstart.supervise_quickstart(
        tmp_path / "pilot",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=log_path,
        ready_file=ready_path,
        max_cpus=1,
        nice_increment=15,
        watchdog_seconds=600,
    )

    assert result == 23
    assert calls == [
        ["quickstart-guardian", "--watchdog"],
        ["quickstart-child"],
    ]
    launch_info = json.loads(ready_path.read_text(encoding="utf-8"))
    assert launch_info["pid"] == supervisor_pid
    assert launch_info["watchdog"]["pid"] == 4322
    assert "supervisor_exits_for_guardian_cleanup" in log_path.read_text(
        encoding="utf-8"
    )


def test_quickstart_launcher_waits_for_supervisor_guardian_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_type = type(tmp_path)
    calls: list[list[str]] = []

    class Supervisor:
        pid = 4321

    def fake_popen(command: list[str], **kwargs: object) -> Supervisor:
        calls.append(command)
        ready_file = path_type(command[command.index("--ready-file") + 1])
        ready_file.write_text(
            json.dumps(
                {
                    "pid": 4321,
                    "process_start_ticks": 987654,
                    "process_kind": "quickstart_supervisor",
                    "watchdog": {
                        "pid": 4322,
                        "process_start_ticks": 987655,
                        "timeout_seconds": 600,
                        "scope": "owned_quickstart_process_group",
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

    result = start_shared_quickstart.start_quickstart(
        tmp_path / "pilot",
        host="127.0.0.1",
        port=8780,
        rpc_port=8545,
        log=tmp_path / "quickstart.log",
        max_cpus=1,
        nice_increment=15,
        watchdog_seconds=600,
    )

    assert len(calls) == 1
    assert "--supervisor" in calls[0]
    assert "--ready-file" in calls[0]
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
def test_posix_shell_cleanup_reclaims_an_orphaned_owned_session_group(
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
            _owned_group_stop_command(process.pid, process_start_ticks),
            shell=True,
            executable=bash,
            check=False,
            timeout=10,
        )

        assert result.returncode == 0
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
            _owned_group_stop_command(process.pid, process_start_ticks),
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

    def verify(script: Path, arguments: list[str]) -> int:
        process = subprocess.Popen(
            [str(script), *arguments],
            start_new_session=True,
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
    ]
    assert verify(canonical, correct) == 0
    assert verify(canonical, [*correct[:2], "4322", *correct[3:]]) == 9
    assert verify(canonical, [*correct, "--watchdog-pid", "4321"]) == 9
    assert verify(canonical, ["--core-watchdog", *correct[1:]]) == 9
    assert verify(canonical, [*correct[:-1], "601"]) == 9
    assert verify(unexpected, correct) == 9


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
    deployment = ".local/share/loveengine-witness-lab/abc123-20260727"
    valid = (
        "/home/daism/"
        + deployment
        + "/tmp/tunnel-pilot/release/loveengine-witness.zip"
    )
    assert _validate_remote_artifact(valid, deployment) == valid

    for value in (
        "/home/daism/.ssh/authorized_keys",
        "/home/daism/" + deployment + "/../other/file.zip",
        "/home/daism/" + deployment + "/file.zip;touch-x",
    ):
        with pytest.raises(RuntimeError):
            _validate_remote_artifact(value, deployment)


def test_owned_process_cleanup_is_pid_and_command_guarded() -> None:
    command = _owned_group_stop_command(4321, 987654)
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
    assert '"\") exit 9' in command
    assert "group_status=$?" in command
    assert 'if [ "$group_status" -eq 1 ]; then exit 0; fi' in command
    assert 'kill -TERM -- "-$pid" 2>/dev/null || true' in command
    assert "loveengine pilot quickstart" in command
    assert "start_shared_quickstart.py --supervisor" in command
    assert "run_core_experiments.py" in command
    assert "start_shared_core.py --supervisor" in command

    with pytest.raises(ValueError):
        _owned_group_stop_command(1, 987654)
    with pytest.raises(ValueError):
        _owned_group_stop_command(4321, 0)


def test_owned_process_absence_check_is_read_only_and_identity_guarded() -> None:
    command = _owned_group_absence_command(4321, 987654)
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

    with pytest.raises(ValueError):
        _owned_group_absence_command(1, 987654)


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
