---
name: create-workflow
description: Add or update an app-local Mozaiks workflow in this generated app workspace.
argument-hint: "[workflow goal]"
disable-model-invocation: true
---

Complete this workflow task: $ARGUMENTS

1. Read `AGENTS.md` and `.claude/rules/workflows.md`.
2. Create or update `workflows/<WorkflowName>/`.
3. Keep workflow YAML structured-output-first and declarative.
4. Put reasoning in agent prompts and structured outputs.
5. Keep tools deterministic: persist, validate, emit events, or call declared APIs.
6. Do not put classification/inference heuristics in tools.
7. Add UI artifact config only when the workflow needs a visual artifact.
8. Update docs if startup behavior, triggers, or required env vars change.
