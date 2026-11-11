# Agent Bootstrap Prompt

<!-- --8<-- [start:local-setup] -->
## Get started with Claude or another coding agent

Clone the [Mozaiks repository](https://github.com/BlocUnited-LLC/mozaiks),
open the repo folder in Claude Code or your preferred coding agent, and copy
this prompt into its chat:

```text
Help me get Mozaiks running locally so I can build my first app.

Read CLAUDE.md, AGENTS.md, and the instructions they reference. Use
docs/getting-started.md for the app setup flow and docs/local-setup.md
for installing this checkout.

Check my operating system and prerequisites. Install from this checkout in
a local Python environment, reuse existing local services where possible,
and create or open an app workspace using the documented CLI flow.

Guide me through configuring any required API keys locally, keeping secret
values out of chat and tracked files. Keep changes focused on local setup
and my app workspace.

Start Studio, verify that it loads, and give me its local URL and the next
step to create my first app.
```
<!-- --8<-- [end:local-setup] -->

## Work on the Mozaiks framework

Use this prompt when you want a coding agent to start with the current Mozaiks
repo boundaries instead of discovering them piecemeal.

```text
You are working in the Mozaiks repo.

Read ARCHITECTURE.md, AGENTS.md, and CLAUDE.md first.

Treat these as canonical:
- Hosts: mozaiksai/hosts/runtime.py, mozaiksai/hosts/platform.py,
  mozaiksai/hosts/studio.py
- Shared builder workflows: factory_app/workflows/
- App-owned workflows: workflows/ under the active app root
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

### When to use this framework prompt

This is the right starting point when the task touches:

- runtime or host layering
- workflow roots and orchestration contracts
- app workspace structure
- Studio/admin ownership
- documentation cleanup against current architecture

### Related

- [Architecture Overview](architecture/index.md)


