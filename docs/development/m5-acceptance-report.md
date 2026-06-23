# M5 LAN pilot acceptance report

状态：release-candidate verification  
日期：2026-06-23  
目标：`0.5.0-lan-pilot`

## Implemented evidence

- Standard thin `SKILL.md` and `agents/openai.yaml`.
- Byte-deterministic ZIP with checksums, SPDX SBOM and safe extraction.
- Authenticated single-process LAN Pilot Server.
- Three outbound-only Agent observation processes with durable cursor recovery.
- Ten concurrent read-only SSE observers.
- Persistent Anvil dump/load with contract address and code-hash checks.
- Explicit per-witness CLI vote approval; Agents never auto-vote.
- One unified package-to-PublicSink `PilotTranscriptV1`.
- Hash-chained audit JSONL and checksum-protected system snapshots.
- Fault injection: one Pilot Server restart, one Anvil restart and three Agent
  disconnects in the accelerated E2E.

## Automated gates

The release candidate is accepted only when all of these pass:

```powershell
uv sync --frozen
uv run python .\tools\check.py
uv run pytest -m "not integration" -q
cd contracts
forge test
cd ..
uv run pytest .\tests\test_demo.py
uv run pytest .\tests\integration\test_network_demo.py
uv run pytest .\tests\integration\test_live_evidence_demo.py
uv run pytest .\tests\integration\test_pilot_chain.py
uv run pytest .\tests\integration\test_pilot_demo.py
uv run pytest .\tests\integration\test_pilot_soak.py
```

## Release gate

The accelerated fault/observer soak is a CI gate, not a substitute for elapsed
time. Tagging `v0.5.0-lan-pilot` additionally requires:

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak
```

The resulting `pilot-soak-report.json`, PilotTranscript, ZIP, checksums and SBOM
are release assets. Until this real four-hour command completes, the version is
a release candidate and must not be described as a completed four-hour trial.

## Wall-clock preflight findings

The first one-hour attempt on 2026-06-23 was a valid failed preflight. It
exposed that the observation client still used the original 30-second default
timeout even while Relay heartbeats kept the connection alive. No final report
was produced, so the run is not accepted.

The protocol now binds `max_duration_seconds` into the signed
`ObserveLiveTextPayloadV1`, capped at 14,700 seconds. Task acceptance ACK
latency is measured separately from final observation completion latency. A
second one-hour run completed the protocol flow, but correctly failed the
resource gate: successful read polling was duplicated into both the
hash-chained audit log and detached-process stdout, and final verification
loaded the resulting audit file into memory. The measured output was
1,663,152,147 bytes and peak RSS was 1,280,077,824 bytes.

Successful read requests are now metrics-only, SSE empty reads use bounded
long-polling instead of busy polling, and audit verification is streaming.
Another one-hour run is required before merge; the four-hour tag gate is
unchanged.
