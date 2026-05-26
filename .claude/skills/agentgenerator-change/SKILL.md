---
name: agentgenerator-change
description: Review or implement a change to AgentGenerator prompts, workflow bundle structured outputs, workflow scaffolds, universal prompt injection, or workflow-agent safety guidance.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches AgentGenerator specifically.

Typical triggers:

- `factory_app/workflows/AgentGenerator/**`
- AgentGenerator workflow prompts
- agent or workflow bundle structured outputs
- generated workflow scaffolds
- generated agent prompts
- generated `handoffs.yaml`, `tools.yaml`, `structured_outputs.yaml`, or `context_variables.yaml`
- universal prompt injection
- MCP or tool capability injection
- workflow or agent safety rules
- agent-generated runtime behavior guidance
- AgentGenerator tests or fixtures

Inspect first:

- `factory_app/workflows/AgentGenerator/agents.yaml`
- `factory_app/workflows/AgentGenerator/structured_outputs.yaml`
- `factory_app/workflows/AgentGenerator/hooks.yaml`
- `factory_app/workflows/AgentGenerator/handoffs.yaml`
- `factory_app/workflows/AgentGenerator/tools.yaml`
- `factory_app/workflows/AgentGenerator/context_variables.yaml`
- `factory_app/workflows/AgentGenerator/tools/hook_universal_prompts.py`
- capability-hook files if they exist in this workflow later, such as `hook_mcp_capabilities.py` or `hook_agent_capabilities.py`
- `factory_app/workflows/AgentGenerator/tools/tool_planning.py`
- `factory_app/workflows/AgentGenerator/tools/workflow_converter.py`
- `factory_app/workflows/AgentGenerator/tools/generate_and_download.py`
- `docs/architecture/workflows/workflow-authoring-contracts.md`
- `docs/architecture/builder/agentgenerator-output-assembly-contract.md`
- `docs/architecture/workflows/workflow-routing-transitions.md`
- the narrowest matching AgentGenerator tests before editing:
  - `tests/test_agentgenerator_workflow_converter.py`
  - `tests/test_agentgenerator_tool_planning.py`
  - `tests/test_agentgenerator_ui_quality_gate.py`
  - `tests/test_agentgenerator_generate_and_download_collection.py`
  - `tests/test_agentgenerator_generate_and_download_persistence.py`

Core truth:

- AgentGenerator is one workflow inside the broader factory build `workflow_sequence`.
- It generates workflow and agent bundles, not app modules or persistent app pages.
- AppGenerator generates app bundle artifacts; AgentGenerator generates AI workflow artifacts.
- It should not own runtime substrate behavior.
- It should not own app page or module generation.
- It should not inject hosted or private product assumptions into generated agents.
- It should produce workflows that respect the current workflow authoring contracts.
- Generated workflow tools, hooks, and helpers stay workflow-local unless the current shared factory infrastructure explicitly owns the shared helper.

Boundary rules:

- Do not treat AgentGenerator as the whole build system.
- Do not change `workflow_sequence` composition from this skill; use `factory-build-workflow-change` when the change widens into `extension_registry.json`, sequence design, transitions, entrypoints, or cross-workflow ownership.
- Do not generate app modules or persistent app pages; that belongs to AppGenerator.
- Do not generate runtime transport, platform, or substrate code.
- Do not inject private hosted-product names, assumptions, or proprietary examples into prompts, scaffolds, tests, or docs.
- Do not use stale workflow fields like `startup_mode` when the current contract uses `workflow_startup_mode`.
- Do not create fake MCP or tool declarations unsupported by runtime or current workflow contracts.
- Do not bypass workflow-local handoff semantics.
- Do not use `workflow_sequence` as a HITL substitute.

Common change types:

1. Prompt changes:
   - inspect `agents.yaml`, `hook_universal_prompts.py`, and the nearest prompt-hygiene tests together
   - preserve universal compliance and lane-discipline guidance
2. Structured output changes:
   - inspect `structured_outputs.yaml` plus the workflow authoring contract docs and converter tests
   - keep field names and enums aligned with current loader expectations
3. Universal prompt hook changes:
   - inspect `hook_universal_prompts.py`, `hooks.yaml`, and tests that validate prompt or UI-safety guidance
   - keep universal behavior separate from file-generation or pattern-specific hooks
4. MCP or tool capability hook changes:
   - inspect `tool_planning.py`, `tools.yaml`, and any capability hook files if present
   - add only runtime-supported tool declarations and workflow UI requirements
5. Workflow scaffold or file contract changes:
   - inspect `workflow_converter.py`, `generate_and_download.py`, and workflow authoring contract docs together
   - keep generated bundle files workflow-local and contract-bound
6. Handoff generation changes:
   - inspect `handoffs.yaml`, converter tests, and authoring docs together
   - preserve workflow-local handoff semantics and exact cross-reference names
7. Tool generation changes:
   - inspect `tools.yaml`, `tool_planning.py`, and workflow UI contract tests together
   - keep tool declarations aligned with supported UI and lifecycle surfaces
8. Workflow safety or human-review guidance:
   - inspect prompt injections, workflow authoring docs, and quality-gate tests together
   - keep HITL semantics explicit inside the workflow bundle, not in `workflow_sequence`
9. Agent role or persona generation:
   - inspect `agents.yaml`, `structured_outputs.yaml`, and downstream scaffold expectations together
   - keep generated agents scoped to workflow behavior, not runtime ownership
10. Test or fixture updates:
   - update the narrowest workflow-bundle fixture that changed
   - keep examples provider-neutral and OSS-safe

Focused testing guidance:

- AgentGenerator structured outputs and generated workflow scaffold behavior:
  - `python -m pytest tests/test_agentgenerator_workflow_converter.py tests/test_agentgenerator_generate_and_download_collection.py tests/test_agentgenerator_generate_and_download_persistence.py -q`
- tool planning and workflow UI/tool declarations:
  - `python -m pytest tests/test_agentgenerator_tool_planning.py tests/test_agentgenerator_ui_quality_gate.py -q`
- workflow authoring contract or field-name changes:
  - pair the nearest AgentGenerator slice with docs-backed checks in the workflow authoring contract surfaces
- universal prompt hygiene or public-safety guidance:
  - `python -m pytest tests/test_agentgenerator_change_skill.py tests/test_claude_guidance_operating_system.py tests/test_contributor_quickstart.py -q`
- build workflow sequence tests only when sequence assumptions change:
  - `python -m pytest tests/test_factory_build_workflow_skill.py tests/test_pack_config_paths.py -q`

Final report requirements:

- Always include `OSS Change Impact`.
- Always include `AgentGenerator Workflow Impact`.
- Include `Build Workflow Sequence Impact` when sequence composition, transitions, entrypoints, or cross-workflow ownership changed.
- Include `Control-Plane / Refinement Impact` when AgentGenerator behavior changed because workflow-bundle refinement routing or checkpoint assumptions changed.

## AgentGenerator Workflow Impact

- AgentGenerator component changed
- generated workflow artifacts affected
- workflow authoring contract affected
- universal prompts or hooks affected
- tests run
- contract drift risk

Return:

1. AgentGenerator component affected
2. generated workflow artifacts affected
3. workflow authoring contract impact
4. universal prompts or hooks affected
5. tests required or run
6. contract drift risk
