"""Local Witness core and optional governance pilot demonstrations."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable

from web3 import HTTPProvider, Web3

from .core_transcript import core_transcript_hash, verify_core_transcript
from .demo import free_port
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .package import install_package, package_self_check
from .pilot_chain import snapshot_chain, start_chain, status_chain, stop_chain
from .pilot_phases import run_pilot_phases
from .pilot_runtime import prepare_local_pilot_runtime


def _scan_pilot_token_leaks(root: Path, token_file: Path) -> list[str]:
    """Return files that contain the Pilot write token, excluding its source."""

    if not token_file.is_file():
        return []
    token = token_file.read_bytes()
    if not token:
        return []
    token_path = token_file.resolve()
    leaks: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.resolve() == token_path:
            continue
        try:
            if token in path.read_bytes():
                leaks.append(path.relative_to(root).as_posix())
        except OSError:
            continue
    return sorted(leaks)


def _finalize_v2_standalone_transcript(
    result: dict[str, Any],
    *,
    output: Path,
    token_file: Path,
    chain_process_exited: bool,
) -> None:
    """Persist honest standalone evidence without claiming outer-process facts."""

    leaks = _scan_pilot_token_leaks(output, token_file)
    transcript = result["transcript"]
    acceptance = transcript["acceptance"]
    acceptance["cleanup_verified"] = False
    acceptance["secret_findings"] = len(leaks)
    acceptance["stderr_empty"] = False
    transcript["transcript_hash"] = core_transcript_hash(transcript)
    write_json(Path(result["transcript_path"]), transcript)

    result["acceptance_verified"] = False
    result["acceptance_evidence"] = {
        "verification_scope": "standalone_process",
        "reason": "external_process_evidence_required",
        "pilot_write_token_scan_completed": True,
        "pilot_write_token_findings": len(leaks),
        "chain_process_exited": chain_process_exited,
        "complete_process_tree_cleanup_verified": False,
        "outer_process_stderr_observed": False,
    }
    result["offline_verification"] = "standalone_unverified"
    if leaks:
        raise LoveEngineError(
            "pilot_secret_findings",
            f"{len(leaks)} Pilot write-token finding(s)",
            4,
        )

    verification = verify_core_transcript(transcript)
    if verification.get("verification_level") != "offline_integrity":
        raise LoveEngineError(
            "offline_transcript_invalid", "WitnessCoreTranscriptV2"
        )
    result["integrity_verification"] = verification["verification_level"]


def _run_pilot_with_platform_loop(
    coroutine: Awaitable[dict[str, Any]],
) -> dict[str, Any]:
    """Run the restart-bearing local Pilot on a loop that closes cleanly."""

    if os.name == "nt":
        # CPython's Proactor loop can race an aiohttp listener restart with a
        # late accept callback. Keep the workaround local to this demo runner.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def run_pilot_demo(
    output: Path,
    *,
    run_id: str | None = None,
    event_count: int = 12,
    observer_count: int = 10,
    event_interval: float = 0.01,
    simulate_faults: bool = True,
    stage: str = "core",
    core_transcript_version: int = 1,
    acceptance_duration_seconds: int | None = None,
) -> dict[str, Any]:
    if stage not in {"core", "governance"}:
        raise LoveEngineError("invalid_pilot_stage", stage)
    if core_transcript_version == 2 and (
        stage != "core"
        or acceptance_duration_seconds != 900
        or event_count != 30
        or observer_count != 10
        or abs(event_interval * event_count - 900) > 0.001
    ):
        raise LoveEngineError(
            "invalid_v2_acceptance_profile",
            "local V2 requires core/900 wall-clock seconds/30 events/10 observers",
        )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server_port = free_port()
    participant_port = free_port() if core_transcript_version == 2 else None
    rpc_port = free_port()
    runtime = prepare_local_pilot_runtime(
        root=output,
        base_url=f"http://127.0.0.1:{server_port}",
        host="127.0.0.1",
        port=server_port,
        rpc_port=rpc_port,
        run_id=run_id or "lan-pilot-e2e-001",
    )
    package = runtime.package
    installed = install_package(
        package.archive,
        output / "installed",
        expected_package_hash=package.keccak256,
    )
    package_self_check(
        output / "installed", expected_package_hash=package.keccak256
    )
    chain_root = output / "chain"
    process_holder = {"chain": runtime.chain_process}
    rpc_url = runtime.config.rpc_url
    w3 = Web3(HTTPProvider(rpc_url))

    async def restart_chain() -> None:
        snapshot_chain(chain_root, rpc_url)
        stop_chain(chain_root, process_holder["chain"])
        process_holder["chain"] = start_chain(chain_root, port=rpc_port)
        runtime.chain_process = process_holder["chain"]
        status_chain(chain_root, rpc_url)

    result: dict[str, Any] | None = None
    try:
        status_chain(chain_root, rpc_url)
        preflight_leaks = _scan_pilot_token_leaks(
            output, runtime.config.token_file
        )
        if preflight_leaks:
            raise LoveEngineError(
                "pilot_secret_findings",
                f"{len(preflight_leaks)} Pilot write-token finding(s)",
                4,
            )
        result = _run_pilot_with_platform_loop(
            run_pilot_phases(
                output,
                w3,
                read_json(chain_root / "deployment.json"),
                runtime,
                event_count=event_count,
                observer_count=observer_count,
                event_interval=event_interval,
                restart_chain=restart_chain,
                simulate_faults=simulate_faults,
                stage=stage,
                core_transcript_version=core_transcript_version,
                participant_port=participant_port,
                acceptance_profile=(
                    {
                        "duration_seconds": 900,
                        "event_count": 30,
                        "observer_count": 10,
                        "restart_verified": simulate_faults,
                        "reconnect_verified": simulate_faults,
                        "cleanup_verified": False,
                        "secret_findings": len(preflight_leaks),
                        "stderr_empty": False,
                    }
                    if core_transcript_version == 2
                    else None
                ),
            )
        )
        result["package_installed"] = installed["installed"]
    finally:
        runtime.chain_process = process_holder["chain"]
        runtime.close()
    if result is None:
        raise LoveEngineError("pilot_result_missing", "pilot phases")
    if core_transcript_version == 2:
        _finalize_v2_standalone_transcript(
            result,
            output=output,
            token_file=runtime.config.token_file,
            chain_process_exited=process_holder["chain"].poll() is not None,
        )
    return result
