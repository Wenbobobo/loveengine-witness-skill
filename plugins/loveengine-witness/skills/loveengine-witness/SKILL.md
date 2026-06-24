---
name: loveengine-witness
description: Verify, install, operate, observe, and audit LoveEngine Witness pilots from Codex. Use for manifests, deterministic packages, invite files, live-text sessions, Agent observation tasks, dispute reviews, explicit witness voting, transcripts, or pilot recovery.
---

# LoveEngine Witness

This plugin is a distribution wrapper for the repository Skill. Treat it as a
thin instruction layer over the versioned protocol runtime.

## Safe start

1. Identify the user's role: Operator, Observation Node, Voting Witness, Public
   Viewer, or Publisher.
2. Verify the manifest or package before joining:
   `loveengine manifest verify` or `loveengine package verify <archive>`.
3. For a local pilot, prefer:
   `loveengine pilot quickstart --root <dir> --headless`.
4. For a participant node, use an invite file plus a signed profile and never
   ask for secrets.

## Boundaries

- Do not request, read, print, store, or transmit private keys, mnemonics,
  keystores, bearer tokens, or access tokens.
- Do not auto-approve witness votes. Voting requires an explicit
  `loveengine witness vote approve` command through an external signer/RPC.
- Relay, dashboards, plugin marketplaces, package mirrors, and live platforms
  are not trust roots. Verify SkillRegistry package hashes and transcript
  hashes.
- Use `docs/api/README.md`, `docs/api/cli-reference.md`, and
  `docs/development/participant-runbook.zh-CN.md` for details instead of
  copying protocol logic into prompts.
