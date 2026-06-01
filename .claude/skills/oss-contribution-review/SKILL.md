---
name: oss-contribution-review
description: Review a proposed or completed OSS change for layer fit, build workflow impact, docs/tests coverage, and contract drift risk before or after editing.
argument-hint: "[change summary or file path]"
---

Review $ARGUMENTS against the Mozaiks OSS contribution contract.

Inspect the owning files first. Start from the nearest concrete anchor and
classify:

1. layer changed: universal substrate, framework capability, or hosted product capability
2. build workflow impact: workflow_sequence, transition, handoff, entrypoint, control-plane route, or none
3. runtime/platform impact
4. app workspace impact
5. tests required
6. docs, rules, or skills required
7. contract drift or public-framing risk

Use these anchors when relevant:

- `AGENTS.md`
- `CLAUDE.md`
- `ARCHITECTURE.md`
- `.claude/skills/README.md`
- `.claude/rules/architecture-boundaries.md`
- `.claude/rules/factory-build-workflows.md`
- `.claude/rules/control-plane-refinement.md`
- `.claude/rules/testing.md`

Return:

1. Layer changed
2. Build workflow impact
3. Runtime/platform impact
4. App workspace impact
5. Tests required/run
6. Docs required/updated
7. Contract drift risk

