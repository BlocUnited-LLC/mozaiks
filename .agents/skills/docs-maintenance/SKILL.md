---
name: docs-maintenance
description: Update or review documentation safely in this repo. Use when renaming Markdown files, updating mkdocs navigation, fixing doc links, modernizing prompt packs, or enforcing lowercase kebab-case doc naming.
argument-hint: "[docs task]"
disable-model-invocation: true
---

Complete this docs task: $ARGUMENTS

Follow these rules:
1. prefer lowercase kebab-case for new Markdown filenames unless the ecosystem requires a convention filename
2. when renaming docs, update relative links and `mkdocs.yml`
3. keep prompt packs aligned to current repo structure and current config ownership
4. avoid broad rewrites unless the task explicitly asks for them

Before finishing, verify that no obvious stale links or filename references remain in the touched area.
