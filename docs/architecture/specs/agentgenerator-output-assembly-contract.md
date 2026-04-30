# AgentGenerator Output Assembly Contract

**Status:** CANONICAL — describes what actually exists
**Last verified:** 2026-04-08
**Source files:**
- `factory_app/app/workflows/AgentGenerator/tools/workflow_converter.py`
- `factory_app/app/workflows/AgentGenerator/tools/generate_and_download.py`
- `factory_app/app/workflows/AgentGenerator/structured_outputs.yaml`
- `factory_app/app/workflows/AgentGenerator/agents.yaml`

> **Related:** [`structured-output-extraction-contract.md`](structured-output-extraction-contract.md) —
> the general runtime contract for structured outputs and auto-tool-call.
> This document covers the **AgentGenerator-specific** pattern where agent outputs
> are accumulated via persistence and assembled into workflow YAML files.

---

## How This Differs From Normal Workflows

Normal mozaiks workflows use the **auto-tool-call pattern**:
agent emits → runtime validates → tool auto-called inline → tool persists/emits UI.

AgentGenerator uses a **deferred assembly pattern**:
agents emit → outputs persist to MongoDB → **DownloadAgent triggers assembly** →
`gather_latest_agent_jsons()` scrapes all prior outputs → `create_workflow_files()` assembles → files written under `MOZAIKS_GENERATED_ARTIFACTS_PATH` → zip presented to user.

There are no auto-tool-call tools per planning agent. Every planning agent's output is
collected in a single final pass by the `DownloadAgent`.

Generated workflow bundles are staged at:

```text
$MOZAIKS_GENERATED_ARTIFACTS_PATH/workflows/{app_id}/{build_id}/{workflow_name}/
```

They do not become active runtime-loaded workflows until an explicit promotion
step copies them into an active app root's `workflows/` directory.

---

## Agent Pipeline

16 agents run in sequence. Each is mapped to a structured output model.

| # | Agent | Output Model | Notes |
|---|-------|-------------|-------|
| 1 | `InterviewAgent` | none (`null`) | Conversational only |
| 2 | `PatternAgent` | `PatternSelectionOutput` | Selects orchestration pattern; feeds `PatternSelection` to all downstream |
| 3 | `WorkflowStrategyAgent` | `WorkflowStrategyOutput` | Defines modules, startup_mode, MFJ decomposition config |
| 4 | `DatabaseIntentAgent` | `DatabaseIntentOutput` | Collections, fields, access patterns → `db_intent.json` |
| 5 | `AgentRosterAgent` | `AgentRosterOutput` | Agent names, module_index, human_interaction; reads `ToolPlanning.ui_requirements` |
| 6 | `ToolPlanningAgent` | `ToolPlanningOutput` | **Absorbs UX decisions**: `ui_requirements` (inline/artifact per module) + backend tools + lifecycle tools + hooks |
| 7 | `ProjectOverviewAgent` | `MermaidSequenceDiagramOutput` | Renders Mermaid sequence diagram as UI artifact; user reviews the agent pipeline before continuing |
| 8 | `ContextVariablesAgent` | `ContextVariablesPlanOutput` | **Absorbs state architecture**: derives context var types, lifecycle_requirements, assets from WorkflowStrategy directly |
| 9 | `ToolsManagerAgent` | `ToolsManifestOutput` | Normalizes tools manifest → `tools.yaml` |
| 10 | `UIFileGenerator` | `UIToolsFilesOutput` | Generates React + Python tool files |
| 11 | `AgentToolsFileGenerator` | `AgentToolsFilesOutput` | Generates Python agent tool stubs |
| 12 | `StructuredOutputsAgent` | `StructuredModelsOutput` | Generates `structured_outputs.yaml` models + registry |
| 13 | `AgentsAgent` | `RuntimeAgentsOutput` | Generates full `agents.yaml` with prompt_sections |
| 14 | `HookAgent` | `HookFilesOutput` | Generates `hooks.yaml` + hook implementations |
| 15 | `HandoffsAgent` | `HandoffRulesOutput` | Generates `handoffs.yaml` |
| 16 | `OrchestratorAgent` | `OrchestrationConfigOutput` | Generates `orchestrator.yaml` |
| 17 | `PackMetadataAgent` | `PackMetadataOutput` | Generates global `extended_orchestration/extension_registry.json` (cross-workflow pack registry) |
| 18 | `DownloadAgent` | `DownloadRequestOutput` | Triggers final assembly |

**Removed agents (collapsed):**
- `StateArchitectAgent` → merged into `ContextVariablesAgent` (context var taxonomy, lifecycle requirements, and assets are derived directly from WorkflowStrategy)
- `UXArchitectAgent` → merged into `ToolPlanningAgent` (UI surface decisions — inline vs artifact — are one field decision per module, part of tool planning)

---

## Output Collection: `gather_latest_agent_jsons()`

When `DownloadAgent` triggers the `generate_and_download` tool, it calls:

```python
collected = await pm.gather_latest_agent_jsons(chat_id=chat_id, app_id=app_id)
```

This method scans MongoDB for assistant messages in the chat, parses their content
as JSON, and returns a dict keyed by `agent_name`:

```python
{
    "WorkflowStrategyAgent": { "WorkflowStrategy": { ... } },
    "AgentsAgent": { "agents": [ ... ] },
    "ContextVariablesAgent": { "context_variables": [ ... ] },
    # ...
}
```

**Collection constraints:**
- Only messages with `role="assistant"` and a valid `agent_name` field
- Only messages whose content parses as valid JSON (after markdown fence stripping)
- Takes the **latest** message per agent name (last turn wins)
- If an agent never emitted valid JSON, its key is absent from `collected`

**Brittleness at scale:** If an agent produces malformed JSON, wraps output in markdown,
or emits multiple turns, only the last valid JSON is kept. Agents that re-ran during
iteration will silently overwrite earlier outputs.

---

## Assembly: `create_workflow_files()`

The collected dict is passed to `create_workflow_files(data, context_variables)`,
which builds a unified `config` dict, then calls `_save_modular_workflow()`.

### Input Key → Config Section Mapping

| Collected Key | Config Section | Target File |
|---------------|---------------|-------------|
| `orchestrator_output` | merged into config root | `orchestrator.yaml` |
| `agents_output.agents` (list→dict transform) | `config["agents"]` | `agents.yaml` |
| `handoffs_output.handoff_rules` | `config["handoffs"]` | `handoffs.yaml` |
| `hooks_output.hooks` (metadata only) | `config["hooks"]` | `hooks.yaml` |
| `context_variables_output` | `config["context_variables"]` | `context_variables.yaml` |
| `tools_manager_output` (tools + lifecycle_tools) | `config["tools"]` | `tools.yaml` |
| `ui_config` | merged into config root | `ui_config.yaml` |
| `structured_outputs` (static) + `structured_outputs_agent_output` (dynamic) | merged → `config["structured_outputs"]` | `structured_outputs.yaml` |

### Files Written by `_save_modular_workflow()`

```
{WorkflowName}/
├── orchestrator.yaml          ← OrchestratorAgent output (merged root keys)
├── agents.yaml                ← AgentsAgent output (list-to-dict transform)
├── handoffs.yaml              ← HandoffsAgent output
├── hooks.yaml                 ← HookAgent metadata (filecontent → extra_files)
├── context_variables.yaml     ← ContextVariablesAgent output
├── tools.yaml                 ← ToolsManagerAgent output
├── ui_config.yaml             ← visual_agents config
├── structured_outputs.yaml    ← merged static + StructuredOutputsAgent
├── capability_spec.json       ← generated from config metadata
├── websocket_config.yaml      ← generated from config metadata
├── db_intent.json             ← DatabaseIntentAgent output (if present)
├── extended_orchestration/
│   └── mfj_extension.json     ← built from WorkflowStrategy.decomposition (if MFJ)
└── tools/
    ├── {tool_name}.py         ← UIFileGenerator + AgentToolsFileGenerator
    └── ...
```

Generated workflow tools are workflow-local. AgentGenerator must not create or
reference `workflows/_shared`, `app.workflows._shared`, sibling workflow tool
folders, or root-level shared tool paths. Reusable framework-owned support code
belongs under `mozaiksai.core.*`; workflow-specific helpers stay beside the
workflow tools that call them.

Frontend React components (if any UI tools):
```
{WorkflowName}/ui/
├── index.js                  ← auto-generated workflow UI barrel
└── components/
    └── {ComponentName}.jsx   ← UIFileGenerator js_content
```

---

## Special Cases

### extended_orchestration/mfj_extension.json (per-workflow MFJ)

**Not** produced by a dedicated agent. Built programmatically in
`_build_workflow_local_pack_graph()` from `WorkflowStrategyAgent`'s output.

Required `WorkflowStrategy.decomposition` fields to trigger graph generation:
```json
{
  "required": true,
  "mode": "single_stage_mfj",
  "decomposition_agent": "DecomposerAgent",
  "child_initial_agent": "WorkerAgent",
  "resume_agent": "ResumeRouterAgent",
  "resume_entry_agent": "ResumeRouterAgent",
  "inject_as": "mfj_results",
  "max_children": 3,
  "contracts": {
    "input_required": ["field1"],
    "input_optional": [],
    "output_required": ["result_field"],
    "output_optional": []
  }
}
```

If `decomposition.required` is false or `mode` is not `single_stage_mfj`,
no `extended_orchestration/mfj_extension.json` is written.

### Structured Outputs Merge

Two sources are merged:

1. **Static base** (`structured_outputs` key in `data`): Model library from the
   AgentGenerator's own `structured_outputs.yaml` — these are the meta-models used
   by the planning agents (e.g., `WorkflowAgent`, `AgentTool`, `ToolSpec`, etc.)

2. **Dynamic** (`structured_outputs_agent_output`): What `StructuredOutputsAgent`
   produced for the **generated** workflow's own models.

The merge strategy: dynamic models and registry entries overwrite static ones.
All agents listed in `agent_names` (from `AgentsAgent` output) are ensured to have
a registry entry (defaulting to `null` if not mapped by `StructuredOutputsAgent`).

### Agents List-to-Dict Transform

`AgentsAgent` emits a list: `[{"name": "MyAgent", "prompt_sections": [...], ...}]`

`create_workflow_files()` transforms this to a dict keyed by agent name:
```python
{"MyAgent": {"prompt_sections": [...], ...}}  # "name" field removed
```

The `name` and `display_name` fields are excluded from the stored config.

### Hook Files

`HookAgent` output includes both:
- Metadata entries (stored in `hooks.yaml`): `hook_type`, `hook_agent`, `filename`, `function`
- Implementation content (`filecontent`): written to `tools/{filename}` as extra files

Function names are normalized (module prefix and dot notation stripped).
Hook implementation files follow the same workflow-local path rule and may not
target shared workflow folders.

---

## Workflow Name Resolution

The generated workflow name is resolved in priority order:

1. `context_variables.action_plan.workflow.name` (auto-tool context injection)
2. `collected["WorkflowStrategyAgent"]["WorkflowStrategy"]["workflow_name"]`
3. `collected["OrchestratorAgent"]["workflow_name"]`
4. Fallback: `"Generated_Workflow"`

All names are converted to PascalCase for the output folder and zip file name.

---

## Agent Output Key Reference

When `WorkflowStrategyAgent` emits:
```json
{
  "WorkflowStrategy": {
    "workflow_name": "StoryCreator",
    "startup_mode": "UserDriven",
    "orchestration_pattern": "PipelinePattern",
    "decomposition": { "required": false }
  }
}
```

`create_workflow_files()` unwraps this via:
```python
payload = raw.get("WorkflowStrategy") or raw.get("workflow_strategy") or raw
```

Other agents follow similar wrapping patterns. The converter handles both wrapped
(`{"WorkflowStrategy": {...}}`) and unwrapped (`{...}`) forms.

---

## At-Scale File Generation Considerations

The current approach has known brittleness points:

| Risk | Description | Mitigation |
|------|-------------|------------|
| **JSON parse failure** | Agent wraps output in markdown or adds extra text | `clean_agent_content()` strips fences; still fails on partial JSON |
| **Agent re-runs** | Iterating agents overwrite earlier outputs | Only latest message per agent is kept — early outputs are lost |
| **Missing agent output** | Agent skipped or failed | Corresponding config section is absent; file may be incomplete |
| **Model mismatch** | StructuredOutputsAgent emits wrong wrapper key | `_normalize_dynamic_structured()` tries common fallback keys |
| **Agents list order** | AgentsAgent list order sets agent sequence | No runtime validation that list order matches handoffs |

**Consistent file generation at scale** requires that each planning agent:
1. Emits exactly one valid JSON turn (no conversational turns before the JSON turn)
2. Uses the canonical wrapper key matching the model name (e.g., `"WorkflowStrategy": {...}`)
3. Does not re-run after its designated turn

To verify output quality, inspect `logs/agent_outputs/agent_outputs_{chat_id}_{ts}.json`
and `logs/workflow_converter/converter_input_{WorkflowName}_{ts}.json` written during
each generation run.

---

## What Does NOT Exist

| Concept from Aspirational Spec | Reality |
|-------------------------------|---------|
| `extraction_contracts.yaml` | Does not exist — mapping is hardcoded in `workflow_converter.py` |
| `BaseExtractor` class / extractor classes | Does not exist — monolithic `create_workflow_files()` function |
| Separate "extraction" phase | Does not exist — assembly happens in `generate_and_download` tool |
| `workflow_strategy.yaml` / `agent_roster.yaml` output files | Intermediate planning only — NOT written as workflow files |
| `StateArchitectAgent` | Removed — its work (context var types, lifecycle requirements, assets) is now done inline by `ContextVariablesAgent` |
| `UXArchitectAgent` | Removed — UI surface decisions (inline/artifact) are now part of `ToolPlanningAgent` output |
| `StateArchitectAgent` | Removed — merged into `ContextVariablesAgent` |
| `UXArchitectAgent` | Removed — merged into `ToolPlanningAgent` |

---

## Cross References

- [structured-output-extraction-contract.md](structured-output-extraction-contract.md) — general auto-tool-call pattern
- [workflow-authoring-contracts.md](../foundations/workflow-authoring-contracts.md) — `extended_orchestration/mfj_extension.json` format
- `factory_app/app/workflows/AgentGenerator/tools/workflow_converter.py` — assembly implementation
- `factory_app/app/workflows/AgentGenerator/tools/generate_and_download.py` — collection + trigger
- `factory_app/app/workflows/AgentGenerator/` — current working MFJ-enabled workflow example
