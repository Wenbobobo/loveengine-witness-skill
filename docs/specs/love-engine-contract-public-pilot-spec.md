# LoveEngine M6 Contract Fusion and Public Pilot Readiness SPEC

Status: active  
Target version: `0.6.0-contract-public-pilot`  
Protocol: `loveengine-witness-net/0.6`  
Updated: 2026-06-24

## 1. Core goal

M6 turns the M5 LAN pilot into a participant-ready pilot that can absorb the
contract team's second contract handoff, explain the remaining changes needed
for protocol safety, and run through a clearer installation/onboarding path.

The trust root remains:

```text
deterministic package hash
→ SkillRegistry release fact
→ signed Agent profile / task / receipt
→ evidence hash chain
→ explicit witness vote
→ on-chain execution
→ offline transcript verification
```

Web pages, Relay, plugin marketplace entries and package mirrors are convenience
layers, not trust roots.

## 2. Baseline

- `v0.5.0-lan-pilot` is the previous release baseline.
- The contract team's v2 handoff is preserved under
  `docs/reference/contracts/contract-team-v2/`.
- Active target contracts remain under `contracts/src/`.
- `SkillRegistry` remains the auxiliary release-trust contract and is not
  counted as one of the four UAS business contracts.
- Tailscale Debian and public testnet validation require external resources and
  are explicit gates, not assumed local checks.

## 3. Milestones

### M6.0 Contract handoff closeout

Goal: make the v2 contract handoff traceable and actionable.

- [ ] Preserve v2 contract files under `docs/reference/contracts/contract-team-v2/`.
  - [ ] Keep Solidity source bytes unchanged.
  - [ ] Add a provenance README.
  - [ ] Ensure the snapshot can compile with Foundry `1.7.1`.
- [ ] Add `docs/development/contract2-comparison-and-recommendations.md`.
  - [ ] Compare all four contracts.
  - [ ] Mark direct adoption, selective adoption, required changes and rejected
        pieces.
  - [ ] Explain the added `SkillRegistry`.
- [ ] Update source inventory and manifest refs.

Acceptance:

- `forge build --contracts ..\docs\reference\contracts\contract-team-v2\contracts`
  succeeds.
- `uv run python .\tools\check.py` succeeds.
- The contract team can read one document and know which changes are required
  before their contracts can become the target ABI.

### M6.1 Contract fusion

Goal: produce an M6 target ABI that keeps current signature safety while
absorbing useful v2 ideas.

- [ ] `WitnessDAO`
  - [ ] Keep OpenZeppelin `EIP712` and `ECDSA`.
  - [ ] Keep register nonce/deadline.
  - [ ] Keep vote proposalId, support, reasonHash, payloadHash, nonce and
        deadline.
  - [ ] Add `createdAt`, `votingDeadline`, `finalizeProposal` and failed
        proposal events.
  - [ ] Add safe relayer refund credits: refund eligibility is separate from
        submission permission.
- [ ] `CorporateSink`
  - [ ] Add indexed `Broadcast[]` metadata.
  - [ ] Keep `liveMetadataHash`.
  - [ ] Bind certificates to broadcast index, session hash and EvidenceBundle
        hash.
  - [ ] Keep configurable interval/window.
- [ ] `StreamingEngine` and `PublicSink`
  - [ ] Keep checkpoint-before-rate-change.
  - [ ] Expose `ratePerUserPerSecond`.
  - [ ] Keep `PublicSink` read-only and interface-based.
- [ ] Python/CLI compatibility
  - [ ] Update ABI/bytecode artifacts if generated artifacts change.
  - [ ] Preserve M2-M5 transcript verification compatibility.

Acceptance:

- Foundry covers wrong signer, wrong proposalId, wrong payloadHash, duplicate
  vote, nonce replay, expired signatures, expired proposal, failed finalize and
  relayer refund credit.
- Python local-loop and LAN pilot demos still reach `PublicSink`.
- `docs/api/loveengine-contract-api.md` matches the target ABI.

### M6.2 Participant onboarding

Goal: make the flow understandable for non-developers.

- [ ] Rewrite `README.zh-CN.md`.
  - [ ] Lead with roles: Operator, Observation Node, Voting Witness, Public
        Viewer and Publisher.
  - [ ] Keep quick start to one main command plus status/verify commands.
  - [ ] Move long command tables to `docs/api/cli-reference.md`.
  - [ ] Explain `/operator/`, `/demo/`, `--open-ui` and `--headless`.
- [ ] Add `docs/development/participant-runbook.zh-CN.md`.
  - [ ] One page per participant role.
  - [ ] Include exact commands and expected outputs.
  - [ ] Include common failure diagnostics.
- [ ] Add `PilotInviteV1`.
  - [ ] Server, Relay, dashboard and package trust facts.
  - [ ] No token, private key, mnemonic or keystore.
  - [ ] Support LAN IP, Tailscale IP and future public domain.

Acceptance:

- A new reader can pick their role and run only the commands for that role.
- Invite verification rejects wrong chainId, registry, publisher and
  packageHash.
- README commands are tested against the actual CLI parser.

### M6.3 Frontend interaction hardening

Goal: make the operator and read-only dashboard usable in bilingual pilot
settings.

- [ ] Add i18n to `/operator/`.
  - [ ] `?lang=zh-CN` and `?lang=en`.
  - [ ] In-page language switch.
  - [ ] No token persistence.
- [ ] Add i18n to `/demo/`.
  - [ ] Same language query support.
  - [ ] Red error state for missing artifact, chain not ready, hash mismatch or
        disconnected nodes.
- [ ] Add `loveengine pilot quickstart`.
  - [ ] `--open-ui` opens the operator and dashboard URLs.
  - [ ] `--headless` prints URLs only.
  - [ ] JSON output includes `operator_url`, `dashboard_url`, `invite_path` and
        `token_file`.
- [ ] Add browser/UI smoke tests and screenshots.

Acceptance:

- Language switching does not reset session state.
- Token never appears in URL, localStorage, fixture, audit log or transcript.
- Manual publish of three events appears on the dashboard within two seconds.

### M6.4 Skill and Plugin distribution

Goal: support realistic installation paths without confusing distribution with
protocol trust.

- [ ] Keep `skills/loveengine-witness/SKILL.md` thin.
- [ ] Add Codex plugin wrapper under `plugins/loveengine-witness/`.
  - [ ] `.codex-plugin/plugin.json`.
  - [ ] bundled or referenced LoveEngine skill.
  - [ ] marketplace example for local/team sharing.
- [ ] Update installation docs.
  - [ ] Plugin path for Codex users.
  - [ ] ZIP install path for non-Codex or headless nodes.
  - [ ] Registry hash verification in both paths.

Acceptance:

- Codex can discover the LoveEngine workflow through the plugin/skill path.
- A fresh directory can install and self-check the ZIP.
- A revoked or mismatched package is rejected even if the plugin is installed.

### M6.5 Tailscale Debian pilot

Goal: prove the pilot can run on a real small server before public domain work.

- [ ] Add Debian runbook.
  - [ ] Python 3.11+, uv, Foundry 1.7.1, SQLite and systemd checks.
  - [ ] Tailscale IP and allowed port check.
- [ ] Add generated service artifacts.
  - [ ] pilot server systemd unit.
  - [ ] persistent Anvil systemd unit.
  - [ ] snapshot timer example.
- [ ] Support `public_base_url`.
  - [ ] Tailscale IP.
  - [ ] LAN IP.
  - [ ] future HTTPS domain.
- [ ] Verify restart recovery.

Acceptance:

- Debian host runs a two-hour pilot.
- Three Agents join from separate roots.
- Ten read-only viewers can load `/demo/`.
- Restart recovery completes within 30 seconds.
- transcript verifies on another machine.

### M6.6 Public domain and testnet readiness

Goal: prepare for public deployment without forcing it before the LAN/Tailscale
pilot is stable.

- [ ] Add public-domain config.
  - [ ] `public_base_url`.
  - [ ] `operator_allowed_origin`.
  - [ ] `tls_mode = none | reverse_proxy`.
  - [ ] Caddy/Nginx examples.
- [ ] Add redacted public metrics mode.
- [ ] Add optional testnet deployment gate.
  - [ ] Sepolia-compatible RPC profile.
  - [ ] secret-file signer only.
  - [ ] deployment manifest with tx hashes, block numbers and explorer URLs.

Acceptance:

- Tailscale URL and public domain URL are generated from the same config model.
- Public read-only dashboard works.
- Write APIs fail without token.
- Testnet gate is skipped unless a funded testnet signer is provided.

### M6.7 Release

Goal: ship `v0.6.0-contract-public-pilot`.

- [ ] Run automated gates:
  - [ ] `uv run python .\tools\check.py`
  - [ ] `uv run pytest -m "not integration" -q`
  - [ ] `forge test`
  - [ ] LAN pilot E2E tests.
- [ ] Produce release assets:
  - [ ] deterministic ZIP;
  - [ ] checksums;
  - [ ] SBOM;
  - [ ] contract recommendations;
  - [ ] Pilot transcript;
  - [ ] Debian run report when available;
  - [ ] UI screenshots;
  - [ ] plugin package or marketplace example.
- [ ] Update docs:
  - [ ] master plan;
  - [ ] README and README.zh-CN;
  - [ ] source inventory;
  - [ ] manifest refs/hashes.

Acceptance:

- Fresh clone plus release ZIP can install, self-check and run the pilot demo.
- Plugin/skill path can discover the workflow.
- Transcript verifies package, events, ObservationSet, EvidenceBundle, review,
  proposal, votes, tx and `PublicSink`.
- Secret scan returns zero.

### M6.8 Release closeout, screenshot runbooks and technical article

Goal: close the M6 branch for review and make the pilot explainable to both
operators and developers without adding protocol surface.

- [ ] Branch review and PR closeout.
  - [ ] Remove local runtime outputs such as `pilot/`, `.tmp/`, browser caches
        and token files before staging.
  - [ ] Re-check the Chinese
        `docs/development/contract2-comparison-and-recommendations.md` for
        terminology, path and status consistency.
  - [ ] Run repository, Python, Foundry, package and documentation gates before
        committing.
  - [ ] Push `feat/loveengine-m6-contract-public-pilot` and open a PR to
        `main`.
  - [ ] Merge only after review and CI are clear, then sync local `main`.
- [ ] Screenshot runbooks.
  - [ ] Keep `docs/development/participant-runbook.zh-CN.md` as the role index.
  - [ ] Add one detailed Chinese runbook for each role:
        Operator, Observation Node, Voting Witness, Public Viewer and
        Publisher.
  - [ ] Store deterministic UI screenshots under `docs/assets/runbooks/`.
  - [ ] Use browser screenshots for UI surfaces and copyable command blocks for
        CLI-only roles.
  - [ ] Ensure screenshots contain no token, private key, mnemonic, keystore or
        RPC secret.
- [ ] Technical architecture article.
  - [ ] Add `docs/articles/loveengine-technical-architecture.zh-CN.md`.
  - [ ] Explain trust roots, backend choices, contract choices, Agent network,
        evidence chain, security boundaries and current limitations.
  - [ ] Include at least two Mermaid diagrams.
  - [ ] Cite Codex Skill and Plugin documents only as distribution context; keep
        SkillRegistry package hash as the protocol trust root.
- [ ] Documentation validation.
  - [ ] Validate runbook image links and image existence.
  - [ ] Reject active references to the removed M5 active SPEC path; link the
        archived copy under `docs/archive/specs/implemented/` when provenance is
        needed.
  - [ ] Keep README screenshots concise and put detailed screenshots in
        runbooks.
  - [ ] Add runbooks and article to source inventory and manifest refs.

Acceptance:

- A host can follow the Operator runbook and identify where to create, publish,
  close and inspect a pilot session.
- A node operator, voting witness, public viewer and publisher can each follow
  only their own runbook without reading implementation code.
- The article accurately marks implemented, active and deferred pieces and does
  not describe Plugin installation as a protocol trust root.
- `uv run python .\tools\check.py`, documentation tests and `git diff --check`
  pass after regenerating manifest hashes.
- All screenshot paths referenced by Markdown exist, are non-empty PNG files and
  pass the secret scan.

## 4. Deferred scope

- Production identity and key custody.
- Public mainnet deployment.
- Production TLS management inside the app.
- P2P or high availability Relay.
- End-to-end encryption.
- Automatic Agent voting.
- Video download, transcoding or multimodal analysis.
- Full enterprise compensation product surface; this moves to M7.
