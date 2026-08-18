# Love Engine Skill showcase

This directory contains the Chinese public project page deployed at
[love-engine-skill.garyryry.chatgpt.site](https://love-engine-skill.garyryry.chatgpt.site).

It has two public routes:

- `/`: Love Engine Skill project overview and reviewed-question workflow.
- `/mtp/`: LLM Multi-Token Prediction research sharing page, with source-backed
  mechanism, acceptance-rate and adoption-checklist interactions.

Open `index.html` directly for local review, or run the Sites-compatible build:

```powershell
node .\showcase\build.mjs
```

The build writes ignored output to `showcase/dist/`, including the Worker entry
point required by Sites. `.openai/hosting.json` contains the non-secret Sites
project ID; deployment credentials are short-lived and must never be committed.

The question form opens a prefilled public GitHub Issue for visitor confirmation.
It does not connect Sites directly to a maintainer computer. See
`docs/development/showcase-question-bridge.zh-CN.md` for the local, read-only Codex
answer workflow and its security boundary.
