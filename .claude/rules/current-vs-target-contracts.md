---
paths:
  - "ARCHITECTURE.md"
  - "README.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
---

# Current Vs Target Contract Rules

Use these rules whenever docs or agent guidance describe architecture, contracts,
or contributor workflows.

- Distinguish current implemented behavior from target or future architecture.
- Label target-state material explicitly as `target`, `planned`, `future`, or
  `canonical target` when the runtime does not implement it yet.
- Do not present aspirational contracts as current unless the code, loader,
  host composition, and tests already implement them.
- When current and target differ, tell contributors which current files and tests
  are the source of truth they must follow today.
- Prefer concrete anchors such as loader code, host startup paths, workflow
  registry files, and tests over generalized prose.