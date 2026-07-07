---
name: setup
description: Set up and verify this generated Mozaiks app workspace locally.
argument-hint: "[optional setup issue]"
disable-model-invocation: true
---

<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
Help set up this app workspace.

1. Create and activate a workspace-local `.venv`.
2. Run `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`.
4. Set `OPENAI_API_KEY` and `MONGO_URI`.
5. Run `.\scripts\run-studio.ps1 -ForceStop` to start Studio.
6. Use Studio to generate and manage app modules and workflows.
<!-- END MOZAIKS MANAGED: agent-guidance -->
