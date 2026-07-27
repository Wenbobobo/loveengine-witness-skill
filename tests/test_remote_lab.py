from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from remote_host_preflight import (  # noqa: E402
    CapacityThresholds,
    evaluate_capacity,
)
from run_remote_lab import (  # noqa: E402
    _owned_group_stop_command,
    _ssh_options,
    _validate_remote_artifact,
    _validate_remote_root,
    _validate_target,
    build_parser,
)
from start_shared_quickstart import _parse_linux_process_start_ticks  # noqa: E402


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
    assert "password" not in build_parser().format_help().lower()


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
    assert "pid=4321" in command
    assert "expected_start=987654" in command
    assert "/proc/$pid/stat" in command
    assert "/proc/$pid/cmdline" in command
    assert "ps -eo pgid=,stat=" in command
    assert "loveengine pilot quickstart" in command
    assert 'kill -TERM -- "-$pid"' in command

    with pytest.raises(ValueError):
        _owned_group_stop_command(1, 987654)
    with pytest.raises(ValueError):
        _owned_group_stop_command(4321, 0)


def test_linux_process_start_ticks_parser_handles_spaced_command_name() -> None:
    prefix = "4321 (uv worker with spaces)"
    fields_after_command = ["S"] + [str(index) for index in range(4, 23)]
    fields_after_command[19] = "987654"
    stat = prefix + " " + " ".join(fields_after_command)

    assert _parse_linux_process_start_ticks(stat) == 987654
