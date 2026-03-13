---
name: runtime-architecture-review
description: Review a runtime change against Application, Run, ExecutionWorker, ExecutionEngine, and Event boundaries in this repo. Use before or after edits in mozaiksai, transport, workers, platform runtime code, orchestration, or adapters.
argument-hint: "[change summary or file path]"
disable-model-invocation: true
---

Review $ARGUMENTS against the Mozaiks runtime architecture.

Return:
1. the affected runtime primitive or primitives
2. the affected architecture layer
3. any boundary leaks, especially product logic inside runtime code
4. risks around engine coupling, tenant isolation, observability, or declarative workflow loading
5. the minimal safe change shape if the current approach is too broad

Use specific file references when possible.
Do not rewrite code unless explicitly asked.