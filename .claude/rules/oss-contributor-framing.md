---
paths:
  - "ARCHITECTURE.md"
  - "README.md"
  - ".env.example"
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "web_shell/**"
---

# OSS Contributor Framing Rules

Use these rules when writing contributor docs, setup guidance, skills, or repo-local
shell guidance.

- Do not present `App Zero` as a required public concept.
- Do not present `mozaiks-app` or any private hosted-product repo as required to
  run or contribute to this OSS repo.
- Frame `factory_app` as the first-party builder/reference app workspace that
  dogfoods the canonical app workspace contract.
- Hosted product workspaces are external consumers of that same contract. Mention
  them only as optional or external when relevant.
- When documenting local UI/dev setup in this repo, prefer the current repo truth:
  `web_shell/`, `scripts/run-studio.ps1`, `scripts/run-backend.ps1`, and
  `scripts/run-frontend.ps1` when those surfaces are the actual implementation.