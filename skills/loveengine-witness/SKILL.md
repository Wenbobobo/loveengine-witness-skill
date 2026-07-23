---
name: loveengine-witness
description: Verify, install, operate, observe, and audit the LoveEngine Witness core and its optional governance experiment. Use for LoveEngine manifests, releases, live-text sessions, Agent observation tasks, evidence bundles, dispute reviews, transcripts, explicit witness voting, or pilot recovery.
---

# LoveEngine Witness

Treat this folder as a thin instruction layer over the versioned LoveEngine
protocol runtime. Do not reproduce protocol logic in prompts.

## Start safely

1. Run `loveengine manifest verify`.
2. Read `skills/loveengine-witness/skill-manifest.json`.
3. Read the active specification and only the API document needed by the task.
4. Use machine-readable CLI output as the execution boundary.

## Common workflows

- Verify or install a release with `loveengine package verify`, `package install`,
  and `package self-check`.
- Operate the LAN service with `loveengine pilot quickstart`, `pilot serve`,
  and `pilot status`.
- Join a pilot node with `loveengine node connect --invite <file> --package <archive> --profile <signed-profile> --rpc-url <url> --address <node>`.
- Observe text only through signed `observe_live_text` tasks.
- Build and finalize evidence before opening a dispute.
- Verify every transcript before reporting an outcome.
- Prepare vote typed data, but require an explicit local witness approval before
  any signature is produced.

## Safety

- Never request, read, print, store, or transmit a raw private key, mnemonic,
  keystore, auth token, or access token.
- Never let an Agent automatically approve a witness vote.
- Treat Relay, HTTP clients, mirrors, dashboards, and live platforms as
  untrusted transports.
- Verify chainId, Registry, Publisher, release status, package hash, recipient,
  nonce, deadline, payload hash, event chain, and artifact hash.
- Keep raw evidence off-chain. Use content hashes in proposals and contracts.
- Do not rewrite preserved source materials.

For detailed interfaces, follow `docs/api/README.md`. For current milestones,
follow `docs/specs/love-engine-master-plan.md`.
