---
paths:
  - "docs/**/*.md"
  - "mkdocs.yml"
  - "*.md"
---

# Docs Rules

Use these rules when editing documentation, prompts, or Markdown in the repo root.

## Naming

Prefer lowercase kebab-case for new Markdown filenames, for example `conversation-modes.md`.

Keep uppercase or convention filenames only when required by the ecosystem, such as:
- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `SKILL.md`

## Editing Rules

When renaming or moving docs:
- update relative Markdown links
- update `mkdocs.yml` navigation when applicable
- avoid stale references to old filenames

Prefer focused edits over broad rewrites.
Preserve the current docs tone unless the user asks for a rewrite.

## Prompt Pack Rules

When editing agent-facing prompt packs:
- use current repo paths, not retired ones
- distinguish runtime config from app-bundle config clearly
- keep instructions specific enough for an agent to follow without guessing

## PyPI README Rules

The root `README.md` is published as the PyPI project description.

When adding README images or demos:
- use absolute HTTPS URLs for media, preferably `https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/...`
- avoid relative image paths such as `./docs/assets/demo.png`; they render on GitHub but break on PyPI
- prefer small PNG/JPG assets for logos over large SVGs
- use absolute GitHub `blob/main/...` links for docs links that should work from PyPI
