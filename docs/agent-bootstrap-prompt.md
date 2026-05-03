# Agent Bootstrap Prompt

Use this prompt when you want a coding agent to start with the current Mozaiks
repo boundaries instead of discovering them piecemeal.

```text
You are working in the Mozaiks repo.

Read ARCHITECTURE.md, AGENTS.md, and CLAUDE.md first.

Treat these as canonical:
- Hosts: mozaiksai/hosts/runtime.py, mozaiksai/hosts/platform.py,
  mozaiksai/hosts/studio.py, mozaiksai/hosts/mozaiks.py
- Shared builder workflows: factory_app/workflows/
- App-owned workflows: app/workflows/ under the active app root
- First-party Studio app bundle: factory_app/app/
- Generated artifacts: generated/

Do not treat these as current canonical contracts:
- runtime_app.py
- platform_app.py
- studio_app.py
- mozaiks_app.py
- platform/ as the canonical app workspace root

Prefer current structured-output-first contracts, layered hosts, and
framework-owned admin/Studio composition.

When editing docs, keep public docs aligned to current runtime/framework
behavior and clearly mark historical material as non-canonical.

Task:
<replace with the exact task you want the agent to perform>
```

## When To Use It

This is the right starting point when the task touches:

- runtime or host layering
- workflow roots and orchestration contracts
- app workspace structure
- Studio/admin ownership
- documentation cleanup against current architecture

## Related

- [Prompt Packs](instruction-prompts/prompt-packs.md)
- [Architecture Overview](architecture/index.md)
