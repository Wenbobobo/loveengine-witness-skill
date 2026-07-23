# LoveEngine M6.8 Demo Documentation and Publication Plan

Status: historical M6 closeout checklist; superseded by 0.6.1 hardening
Target version: `0.6.0-contract-public-pilot`
Last updated: 2026-06-24
Archive note: unchecked items preserve the original planning state.

This file preserves the M6.8 execution checklist. Unchecked boxes are historical
planning state, not claims about the current repository. The local
`v0.6.0-contract-public-pilot` baseline was completed; Tailscale, public/testnet
and production gates were not completed and remain deferred.

## 1. Objective

M6.8 is a release closeout stage. It does not add protocol capability. It makes
the current M6 pilot reviewable, publishable and understandable:

```text
review current M6 branch
→ remove runtime secrets and caches
→ add role-based screenshot runbooks
→ publish a technical architecture article
→ strengthen documentation validation
→ run verification
→ push PR to main
```

## 2. Work packages

### 2.1 Branch and repository cleanup

- [ ] Confirm the branch is `feat/loveengine-m6-contract-public-pilot`.
- [ ] Remove local runtime directories such as `pilot/`, `.tmp/`,
      `.pw-browsers*` and `.npm-cache`.
- [ ] Confirm `docs/reference/contracts/contract-team-v2/` is preserved as
      reference input and not rewritten.
- [ ] Review `docs/development/contract2-comparison-and-recommendations.md`
      for terminology and path consistency.

Acceptance:

- `git status --short` contains no runtime cache, token, browser binary or
  local pilot state.

### 2.2 Screenshot runbooks

- [ ] Generate deterministic UI screenshots from the current operator and
      dashboard HTML.
- [ ] Keep screenshots under `docs/assets/runbooks/`.
- [ ] Add one detailed Chinese runbook per role.
- [ ] Keep CLI-only steps as copyable commands and expected JSON snippets.

Acceptance:

- Each referenced screenshot path exists and is larger than 1 KiB.
- Screenshots do not contain token, private key, mnemonic, keystore or RPC
  secret strings.

### 2.3 Technical article

- [ ] Write `docs/articles/loveengine-technical-architecture.zh-CN.md`.
- [ ] Cover trust roots, Python runtime, contracts, Agent network, evidence
      chain, security boundaries and deferred production features.
- [ ] Include at least two Mermaid diagrams.

Acceptance:

- The article is technical rather than promotional.
- Implemented, active and deferred work are explicitly separated.

### 2.4 Validation

- [ ] Extend `tools/validate_docs.py` to check runbook image links.
- [ ] Ensure active docs do not reference the removed M5 active SPEC path; use
      `docs/archive/specs/implemented/love-engine-lan-pilot-spec.md` for
      provenance links.
- [ ] Update source inventory, `sources.json` and manifest refs.
- [ ] Refresh manifest hashes.

Acceptance:

- `uv run python .\tools\check.py` passes.
- `uv run pytest tests/test_documentation.py -q` passes.
- `git diff --check` passes.

### 2.5 Pull request

- [ ] Commit related changes in small, reviewable commits.
- [ ] Push the current branch.
- [ ] Open a draft PR against `main`.
- [ ] Add verification evidence to the PR body.

Acceptance:

- The PR contains no runtime secret or local cache.
- The PR body lists exact commands run and any unavailable external gates.

## 3. Non-goals

- No M7 enterprise compensation work.
- No production TLS, high availability Relay, P2P or public testnet deployment.
- No automatic Agent voting.
- No rewrite of the contract-team reference source.
