# M5 LAN pilot acceptance report

状态：implemented baseline; four-hour soak evidence remains separate
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

The M5 automated baseline is accepted when all of these pass:

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

## Four-hour run evidence

The accelerated fault/observer soak is a CI gate, not a substitute for elapsed
time. A release-quality public report should still include:

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak
```

The resulting `pilot-soak-report.json`, PilotTranscript, ZIP, checksums and SBOM
are release assets. If the real four-hour command has not been rerun in a fresh
environment, describe M5 as implemented with accelerated and one-hour preflight
evidence, not as a completed four-hour field trial.

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

The third run completed the full one-hour wall-clock preflight and passed every
gate:

- 60 committed events, three signed Agent receipts and ten observers.
- One Pilot Server restart, one Anvil restart and three Agent disconnects.
- Recovery: 2.160 seconds.
- Task acceptance ACK p95/max: 59.635 ms.
- Output: 2,257,727 bytes; peak RSS: 160,002,048 bytes.
- Transcript SHA-256:
  `c7bcd1c76e427f3427a8c1d573ce8048736e2c101eafe3b3b5ea354dd9fc7717`.
- Report SHA-256:
  `c24fb2b6f5e7fc58524a7d3af814b755178732456443f6bbcbaa6729900b6a81`.
- Transcript verification returned 60 events, three observation receipts, five
  explicit vote approvals and `valid: true`; secret scan returned zero.

The one-hour merge gate is therefore satisfied. The separate four-hour field
evidence remains useful for future operational reports.
