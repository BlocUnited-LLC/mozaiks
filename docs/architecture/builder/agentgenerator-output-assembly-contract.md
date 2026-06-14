# AgentGenerator Output Assembly Contract

**Status:** CANONICAL — describes what actually exists
**Last verified:** 2026-06-12
**Source files:**
- `factory_app/workflows/AgentGenerator/tools/generate_and_download.py`
- `factory_app/workflows/AgentGenerator/tools/workflow_converter.py`
- `factory_app/workflows/AgentGenerator/structured_outputs.yaml`
- `factory_app/workflows/AgentGenerator/agents.yaml`
- `factory_app/workflows/AgentGenerator/extended_orchestration/task_batches.yaml`

> **Related:** [`structured-output-extraction-contract.md`](../workflows/structured-output-extraction-contract.md) —
> the general runtime contract for structured outputs and auto-tool-call.
> This document covers the **AgentGenerator-specific** pattern where a task batch
> worker agent produces all workflow bundle files in a single structured output.

---

## How This Works

AgentGenerator uses the **task batch pattern**:

1. `PackBuildCoordinator` collects the user's workflow pack specification into `workflows_spec` (context variable)
2. The `workflow_generation_tasks` task batch fires — one `WorkflowBundleBuilderAgent` instance per workflow in the pack, running in parallel
3. Each worker emits a `WorkflowBundleBuilderOutput` with a `files` list of `CodeFile` entries
4. The runtime collects all results into `context_variables["workflow_bundle_results"]`, keyed by task_id
5. `generate_and_download` reads `workflow_bundle_results`, writes each bundle to disk, zips them, and presents the download UI

There are no sequential planning agents scraping MongoDB. Each `WorkflowBundleBuilderAgent` instance owns its full bundle output end-to-end.

Generated workflow bundles are staged at:

```text
$MOZAIKS_GENERATED_ARTIFACTS_PATH/workflows/{app_id}/{workflow_name}/
```

They do not become active runtime-loaded workflows until an explicit promotion
step copies them into an active app root's `workflows/` directory.

---

## Agent Roster

| Agent | Role |
|-------|------|
| `PatternAgent` | Selects orchestration pattern; output available to `WorkflowBundleBuilderAgent` via context |
| `PackBuildCoordinator` | Interviews the user, populates `pack_spec`, seeds the task batch |
| `WorkflowBundleBuilderAgent` | Task batch worker — generates all YAML and code files for one workflow bundle |
| `PackMetadataAgent` | Produces pack-level `extension_registry.json` metadata after workflow bundles complete |
| `DownloadAgent` | Triggers `generate_and_download` after pack metadata is ready |

---

## Task Batch: `workflow_generation_tasks`

Declared in `extended_orchestration/task_batches.yaml`.

```yaml
source:
  kind: context_variable
  path: workflows_spec
  task_model: WorkflowInPack
worker:
  mode: ag2_agent
  agent_field: initial_agent   # WorkflowBundleBuilderAgent
  prompt_field: initial_message
result:
  context_key: workflow_bundle_results
  status_key: workflow_bundle_status
```

Each task receives one `WorkflowInPack` entry from `workflows_spec`.
The runtime injects it as `context_variables["structured_output"]` for the worker.

---

## WorkflowBundleBuilderOutput

The worker emits a single `WorkflowBundleBuilderOutput` structured output:

```yaml
workflow_name: str
files:
  - filename: orchestrator.yaml
    content: "..."
  - filename: agents.yaml
    content: "..."
  - filename: transition_graph.yaml
    content: "..."
  - filename: context_variables.yaml
    content: "..."
  - filename: structured_outputs.yaml
    content: "..."
  - filename: tools.yaml
    content: "..."
  - filename: middleware.yaml
    content: "..."
  - filename: ui_config.yaml
    content: "..."
  - filename: tools/some_tool.py
    content: "..."
  - filename: ui/index.js
    content: "..."
```

Each `CodeFile` has:
- `filename` — workflow-local relative path (e.g., `tools/save_result.py`, `ui/index.js`)
- `content` — full file content as a string

---

## workflow_bundle_results Structure

After the task batch completes, `context_variables["workflow_bundle_results"]` is a dict:

```python
{
    "task_id_1": {
        "workflow_name": "StoryCreator",
        "files": [
            {"filename": "orchestrator.yaml", "content": "..."},
            {"filename": "agents.yaml", "content": "..."},
            ...
        ]
    },
    "task_id_2": {
        "workflow_name": "ReviewWorkflow",
        "files": [ ... ]
    }
}
```

Keys starting with `_` are internal meta entries and are skipped during assembly.

---

## Assembly: `generate_and_download`

Reads `workflow_bundle_results` directly from context. For each bundle entry:

1. `_write_bundle_to_disk(wf_name, files, base_dir)` — writes all `CodeFile` entries under `base_dir/{wf_name}/`
2. `_build_pack_zip(bundle_dirs, output_path)` — zips all workflow directories into a single archive
3. `use_ui_tool("DownloadCenter", ...)` — presents the download UI to the user

Assembly reads task-batch structured outputs directly from runtime context.

### Workflow Integration Metadata

During bundle assembly, `generate_and_download` derives
`workflow_integration_metadata` from each generated workflow's
`orchestrator.yaml`:

- `workflow_name`
- derived stable `capability_id`
- `workflow_startup_mode`
- event triggers from the `triggers[]` block

The normalized metadata is written back to context as:

- `workflow_integration_metadata`
- `generated_workflow_integrations`
- `generated_workflow_name`
- `generated_workflow_capability_id`
- `generated_workflow_startup_mode`
- `generated_workflow_trigger_events`

AgentGenerator persists the same metadata on the `workflow_bundle` artifact.
AppGenerator hydrates it from the latest current `workflow_bundle` artifact
before agents run, then its deterministic acceptance gate blocks export unless
the generated app wires the workflow through module capabilities, emitted
events, and `contracts/reactions.yaml`.

Smoke coverage:

```bash
python scripts/smoke_factory_artifact_lineage.py
python scripts/smoke_factory_artifact_lineage.py --real-store
python scripts/smoke_factory_artifact_lineage.py --real-store --live-agentgenerator --timeout-seconds 600
```

The first command runs the deterministic in-memory chain. The second uses the
Mongo-backed `ArtifactStore`. The third runs live AgentGenerator AG2 calls,
persists the live workflow metadata through the real artifact store, hydrates
AppGenerator from that `workflow_bundle`, and verifies app acceptance/export and
runtime loader reaction wiring.

`generate_and_download` runs the workflow bundle quality gate before download,
artifact registration, zip creation, or promotion. The gate writes
`workflow_bundle_validation_status`, `workflow_bundle_validation_errors`, and
`workflow_bundle_semantic_drift` to context. Blocking failures return
`status: blocked`; the generated bundle is not packaged.

The live AgentGenerator pack smoke also emits the same `semantic_drift` report.
That report is intentionally prompt-oriented: it flags generated workflow YAML
that loads but no longer preserves the requested workflow meaning, such as event
triggers with missing `capability_id`, generic trigger descriptions, or conveyor
workflows that collapse downstream parallel work into one execution agent. Fix
those failures in AgentGenerator prompt or structured-output contracts first,
then rerun the live smoke.

### Files Written

```
$MOZAIKS_GENERATED_ARTIFACTS_PATH/workflows/{app_id}/
├── {WorkflowName}/
│   ├── orchestrator.yaml
│   ├── agents.yaml
│   ├── transition_graph.yaml
│   ├── context_variables.yaml
│   ├── structured_outputs.yaml
│   ├── tools.yaml
│   ├── middleware.yaml
│   ├── ui_config.yaml
│   ├── extended_orchestration/
│   │   └── task_batches.yaml   ← if workflow uses task batches
│   ├── tools/
│   │   └── *.py               ← tool implementations
│   └── ui/
│       ├── index.js            ← component barrel
│       └── *.jsx               ← workflow-local React components
└── {PackName}.zip             ← all workflows bundled together
```

Generated workflow tools are workflow-local. Generated bundles must not reference
`workflows/_shared`, sibling workflow tool folders, or root-level shared paths.
Reusable framework-owned support code belongs under `mozaiksai.core.*`.

---

## Workflow Name and Pack Name

- `bundle_name` is derived from `context_variables["pack_name"]` if set, otherwise from the first workflow's `workflow_name`
- All names are converted to PascalCase for the output folder and zip file name

---

## Normalization Utilities in `workflow_converter.py`

`workflow_converter.py` is a contract normalization helper — it does NOT do assembly.
Functions exported:

| Function | Purpose |
|----------|---------|
| `promote_generated_workflow(source_dir, target_root)` | Copy a generated workflow into the active workflows root |
| `_normalize_transition_rules(raw_rules)` | Normalize transition rule list; rejects LLM-evaluated conditions |
| `_normalize_tools_manifest(output, wf_logger)` | Normalize tools + lifecycle_tools with UI realization stamps |
| `_normalize_visual_agents(value, workflow_startup_mode)` | Normalize visual_agents list per workflow_startup_mode |
| `_collect_ui_code_files(output, tools_config, wf_logger)` | Collect UIFileGenerator output, skip shipped primitives, synthesize barrel |
| `_normalize_runtime_extensions(extensions, workflow_name, wf_logger)` | Keep extensions workflow-local |
| `_normalize_orchestrator_triggers(triggers, wf_logger)` | Validate trigger types against declared schema |

These are used by tests and by `WorkflowBundleBuilderAgent` implementations as reference
for what a well-formed bundle looks like.

---

## Current Boundaries

AgentGenerator's runtime path is a compact task-batch workflow:

- `PatternAgent` selects the workflow topology and AG2 network pattern.
- `ProjectOverviewAgent` presents the generated-workflow plan for review.
- `PackBuildCoordinator` triggers `workflow_generation_tasks`.
- `WorkflowBundleBuilderAgent` workers generate complete workflow bundles in parallel.
- `PackMetadataAgent` generates pack-level routing metadata.
- `DownloadAgent` packages the generated artifacts.

Bundle assembly is context-first: generated workflow files come from
`workflow_bundle_results`, and pack metadata comes from the current structured
outputs. Runtime loading still requires explicit promotion into an active
workspace workflow root.

---

## Cross References

- [structured-output-extraction-contract.md](../workflows/structured-output-extraction-contract.md) — general auto-tool-call pattern
- [workflow-authoring-contracts.md](../workflows/workflow-authoring-contracts.md) — `extended_orchestration/task_batches.yaml` format
- `factory_app/workflows/AgentGenerator/tools/generate_and_download.py` — assembly and download
- `factory_app/workflows/AgentGenerator/tools/workflow_converter.py` — normalization utilities
- `factory_app/workflows/AgentGenerator/extended_orchestration/task_batches.yaml` — task batch config
- `factory_app/workflows/AgentGenerator/structured_outputs.yaml` — `WorkflowBundleBuilderOutput` model

