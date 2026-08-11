# M3 acceptance report

Baseline date: 2026-06-22  
Release target: `0.3.1-demo-ready`

## Verified results

- Repository checks: M0 validation, tamper rejection, source inventory and active documentation checks passed.
- Python: 38 tests passed.
- Foundry 1.7.1: 19 tests passed.
- Local loop: five witnesses registered; proposal executed; PublicSink query and transcript verification passed.
- Network loop: three Agent processes, six queued tasks, eight deliveries, six acknowledgements, three intentional rejections, zero connected nodes after shutdown.
- Network transcript: three nodes, six tasks and six signed receipts verified.
- PR #1, #2 and #3 are merged; their review threads are resolved.

## Known non-blocking item

Web3 currently imports `websockets.legacy`, producing an upstream deprecation warning. It does not affect the protocol or test result and should be removed through a dependency upgrade rather than local suppression.
