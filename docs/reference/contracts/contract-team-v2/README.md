# Contract team v2 source snapshot

Status: preserved source input
Received: 2026-06-24
Original local path: `contracts2/`
Current path: `docs/reference/contracts/contract-team-v2/`

This directory preserves the contract team's second contract handoff as source
material. The Solidity files are kept as received and are not the active target
ABI by themselves.

Active implementation remains under `contracts/src/`. The comparison,
recommendations, and accepted integration path are documented in
`docs/development/contract2-comparison-and-recommendations.md`.

## Contents

- `contracts/WitnessDAO.sol`
- `contracts/CorporateSink.sol`
- `contracts/StreamingEngine.sol`
- `contracts/PublicSink.sol`

## Local compile check

The snapshot compiled under the repository Foundry configuration with
Foundry `1.7.1` and solc `0.8.26`:

```powershell
cd contracts
forge build --contracts ..\docs\reference\contracts\contract-team-v2\contracts
```

Compilation success is only a syntax and dependency check. It is not equivalent
to accepting the contract set as the protocol ABI.
