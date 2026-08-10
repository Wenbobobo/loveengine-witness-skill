"""Local Witness core and optional governance pilot demonstrations."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable

from web3 import HTTPProvider, Web3

from .demo import free_port
from .errors import LoveEngineError
from .jsonio import read_json
from .package import install_package, package_self_check
from .pilot_chain import snapshot_chain, start_chain, status_chain, stop_chain
from .pilot_phases import run_pilot_phases
from .pilot_runtime import prepare_local_pilot_runtime


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
) -> dict[str, Any]:
    if stage not in {"core", "governance"}:
        raise LoveEngineError("invalid_pilot_stage", stage)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server_port = free_port()
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

    try:
        status_chain(chain_root, rpc_url)
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
            )
        )
        result["package_installed"] = installed["installed"]
        return result
    finally:
        runtime.chain_process = process_holder["chain"]
        runtime.close()
