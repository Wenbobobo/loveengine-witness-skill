from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import run_remote_lab  # noqa: E402
import run_core_experiments  # noqa: E402
from remote_host_preflight import (  # noqa: E402
    CapacityThresholds,
    _parse_process_snapshot,
    evaluate_capacity,
)
from run_remote_lab import (  # noqa: E402
    TUNNEL_NODE_COUNT,
    _owned_group_absence_command,
    _owned_group_stop_command,
    _no_forwarding_options,
    _ssh_options,
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
    validate_shared_host_limits as validate_quickstart_limits,
)


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


def test_core_runner_accepts_only_exact_multiline_foundry_version() -> None:
    assert run_core_experiments.is_exact_forge_version(
        "forge Version: 1.7.1\n"
        "Commit SHA: 4072e48705af9d93e3c0f6e29e93b5e9a40caed8\n"
    )
    assert not run_core_experiments.is_exact_forge_version(
        "forge Version: 11.7.10\n"
    )
    assert not run_core_experiments.is_exact_forge_version(
        "wrapper output\nforge Version: 1.7.1\n"
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
    assert "ControlMaster=no" in options
    assert "ForwardAgent=no" in options
    assert options[options.index("-F") + 1] in {"NUL", "/dev/null"}
    assert "password" not in build_parser().format_help().lower()
    assert _no_forwarding_options() == [
        "-o",
        "ClearAllForwardings=yes",
    ]


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
    assert "ps -eo pgid=,stat=" in command
    assert "snapshot=$(ps -eo pgid=,stat=) || return 0" in command
    assert 'kill -TERM -- "-$pid" 2>/dev/null || true' in command
    assert "loveengine pilot quickstart" in command
    assert "run_core_experiments.py" in command

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
    assert "ps -eo pgid=,stat=" in command
    assert "2>/dev/null || true" in command
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
    result = {
        "rejected": 0,
        "receipts": [
            {
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
        )["status"]
        == "completed"
    )
    result["receipts"][0]["result"]["evidence_verified"] = False
    with pytest.raises(RuntimeError, match="evidence-verified"):
        _validated_review_receipt(
            result,
            task_id="task-2",
            dispute_id="dispute-2",
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
    lab.args = Namespace(host="test-host", output=tmp_path)
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
    saved = tmp_path / "remote-lab-report.json"
    assert '"status": "blocked"' in saved.read_text(encoding="utf-8")
