from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from web3 import HTTPProvider, Web3

from loveengine_witness.demo import free_port
from loveengine_witness.jsonio import read_json, write_json
from loveengine_witness.m4_network import build_task_v2
from loveengine_witness.m4_typed_data import build_task_v2_typed_data
from loveengine_witness.pilot_runtime import prepare_local_pilot_runtime
from loveengine_witness.pilot_server import RELAY_KEY, create_pilot_app
from loveengine_witness.pilot_task_operator import enqueue_review_task


@pytest.mark.integration
def test_quickstart_runtime_exposes_trust_bound_public_node_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        server_port = free_port()
        rpc_port = free_port()
        base_url = f"http://127.0.0.1:{server_port}"
        runtime = prepare_local_pilot_runtime(
            root=tmp_path,
            base_url=base_url,
            host="127.0.0.1",
            port=server_port,
            rpc_port=rpc_port,
        )
        app = create_pilot_app(
            runtime.config,
            bootstrap=runtime.bootstrap,
            releases={runtime.release_key: runtime.release},
            package_artifacts={
                runtime.package.keccak256.lower(): runtime.package.archive.read_bytes()
            },
        )
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", server_port).start()
        try:
            async with ClientSession() as session:
                health = await session.get(base_url + "/healthz")
                assert health.status == 200
                assert (await health.json())["status"] == "ok"

            profile_path = Path(runtime.info["profiles"][0])
            node = read_json(profile_path)["profile"]["node"]
            dispute_id = "quickstart-late-dispute"
            verdicts_path = tmp_path / "late-task-verdicts.json"
            write_json(verdicts_path, {dispute_id: "dismiss"})
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "loveengine_witness.cli",
                "node",
                "connect",
                "--invite",
                str(tmp_path / "pilot-invite.json"),
                "--trust-policy",
                str(tmp_path / "pilot-trust-policy.json"),
                "--package",
                str(runtime.package.archive),
                "--profile",
                str(profile_path),
                "--rpc-url",
                runtime.config.rpc_url,
                "--address",
                node,
                "--verdicts",
                str(verdicts_path),
                "--expected-tasks",
                "1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            for _ in range(100):
                if Web3.to_checksum_address(node) in app[RELAY_KEY].connected:
                    break
                await asyncio.sleep(0.05)
            assert Web3.to_checksum_address(node) in app[RELAY_KEY].connected

            w3 = Web3(HTTPProvider(runtime.config.rpc_url))
            task = build_task_v2(
                chain_id=runtime.config.chain_id,
                registry=runtime.release["registry"],
                task_id="quickstart-late-task",
                task_type="review_dispute",
                issuer=runtime.release["publisher"],
                recipient=node,
                manifest_hash=runtime.release["manifest_hash"],
                payload={
                    "dispute_id": dispute_id,
                    "bundle_hash": "0x" + "12" * 32,
                },
                nonce="9001",
                deadline=runtime.bootstrap["valid_until"],
            )
            typed = build_task_v2_typed_data(task)
            signed = w3.provider.make_request(
                "eth_signTypedData_v4",
                [
                    runtime.release["publisher"],
                    json.dumps(typed, separators=(",", ":")),
                ],
            )
            assert "error" not in signed
            task["signature"] = str(signed["result"])
            async with ClientSession() as session:
                missing_auth = await session.post(
                    base_url + "/v1/relay/tasks",
                    json=task,
                )
                assert missing_auth.status == 401
                wrong_auth = await session.post(
                    base_url + "/v1/relay/tasks",
                    json=task,
                    headers={"Authorization": "Bearer wrong"},
                )
                assert wrong_auth.status == 401
                wrong_origin = await session.post(
                    base_url + "/v1/relay/tasks",
                    json=task,
                    headers={
                        "Authorization": f"Bearer {runtime.config.write_token}",
                        "Origin": "https://untrusted.example",
                    },
                )
                assert wrong_origin.status == 403
                queued_value = await asyncio.to_thread(
                    enqueue_review_task,
                    tmp_path,
                    profile_index=1,
                    task_id=task["task_id"],
                    dispute_id=dispute_id,
                )
                assert queued_value["queued"] is True
                assert queued_value["task_id"] == task["task_id"]
                assert queued_value["recipient"] == node
                duplicate = await session.post(
                    base_url + "/v1/relay/tasks",
                    json=task,
                    headers={
                        "Authorization": f"Bearer {runtime.config.write_token}",
                        "Origin": base_url,
                    },
                )
                assert duplicate.status == 409

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
            result = json.loads(stdout.decode("utf-8"))
            assert result["node"] == node
            assert result["rejected"] == 0
            assert len(result["receipts"]) == 1
            assert result["receipts"][0]["task_id"] == task["task_id"]
            assert app[RELAY_KEY].store.metrics()["acked"] == 1
        finally:
            await runner.cleanup()
            runtime.close()

    asyncio.run(scenario())
