# Context Variables (Canonical + AgentGenerator Resolution)

## Status

This document defines:

- the runtime context variable contract
- how values are actually resolved at load time
- the concrete variable-by-variable acquisition logic for `AgentGenerator`

## Canonical File

`app/workflows/{workflow}/context_variables.yaml`

## Canonical Contract Shape

```yaml
definitions:
  variable_name:
    type: string
    description: Optional description
    source:
      type: state
      default: null

agents:
  AgentName:
    variables:
      - variable_name
```

Rules:

- `definitions` is a map (`name -> definition`).
- `agents` is a map (`agent_name -> {variables: [...]}`).
- unknown fields are rejected.

## Runtime Resolution Semantics

Source types supported by runtime:

- `config`
- `data_reference`
- `data_entity`
- `computed`
- `state`
- `external`
- `file`

### `config`

- Reads `source.env_var` from environment.
- If env var is missing, falls back to `source.default`.
- If `source.required` is `true` and value is still missing, load fails.
- Applies type coercion for booleans and integers.

### `data_reference`

- Resolves adapter via `get_db_adapter(source)`.
- Materializes `query_template` placeholders:
  - `{{runtime.app_id}}` -> current app id
  - `{{runtime.<key>}}` -> context value lookup
  - `{{some_var.path}}` -> context dereference
- Uses `fields` to build projection.
- If one field is requested, returns that single field value.
- If no matching document:
  - returns `source.default` when provided
  - otherwise `None`

### `data_entity`

- Creates a `DataEntityManager` for workflow-owned writes.
- Injects manager object into context key.

### `computed`

- Initializes key to `None` at load.
- Expected to be populated later by tools/agent execution.

### `state`

- Initializes key to `source.default` at load (with type coercion).
- Trigger metadata is declared in config and used by runtime mechanisms for updates.

### `external`

- Initializes key to `None` at load.
- Intended to be populated by integration tools.

### `file`

- Resolves relative paths from repo root.
- Enforces repo-root safety (unless `CONTEXT_FILE_ALLOW_OUTSIDE_ROOT=true`).
- Denies common secret files (`.env`, `.pem`, `.key`, etc.).
- Parses by `format`:
  - `json` (default)
  - `yaml`
  - `text`
- Falls back to default if not required and missing/invalid.

## AgentGenerator Context Variables

Source file analyzed:

- `factory_app/workflows/AgentGenerator/context_variables.yaml`

### Resolution Matrix

| Variable | Type | Source | How it is obtained at runtime |
|---|---|---|---|
| `context_aware` | `boolean` | `config` | `os.getenv("CONTEXT_AWARE")`, fallback `true`, boolean coercion |
| `concept_overview` | `string` | `data_reference` | Mongo lookup in `autogen_ai_agents.Concepts` with query `{app_id: {{runtime.app_id}}}`, field `ConceptOverview` |
| `concept_api_endpoints` | `array` | `data_reference` | Same query path as above, field `ApiEndpoints`, fallback `[]` when no doc/value |
| `concept_blueprint` | `object` | `data_reference` | Same query path as above, field `Blueprint`, fallback `null` |
| `monetization_enabled` | `boolean` | `config` | `os.getenv("MONETIZATION_ENABLED")`, fallback `false`, boolean coercion |
| `context_include_schema` | `boolean` | `config` | `os.getenv("CONTEXT_INCLUDE_SCHEMA")`, fallback `false`, boolean coercion |
| `context_schema_db` | `string` | `config` | `os.getenv("CONTEXT_SCHEMA_DB")`, fallback `null` |
| `interview_complete` | `boolean` | `state` | Initialized to `false`; set through state trigger (`agent_text`, `InterviewAgent`, `match.equals=NEXT`, `ui_hidden=true`) |
| `workflow_review_approved` | `boolean` | `state` | Initialized to `false`; set through a `user_text` regex when the user approves the review step in the main chat composer |
| `workflow_review_revision_requested` | `boolean` | `state` | Initialized to `false`; set through a `user_text` regex when the user asks for revisions in the main chat composer |
| `action_plan` | `object` | `computed` | Starts `None`; set by workflow tools (for example action plan generation tool chain) |
| `workflow_strategy` | `object` | `computed` | Starts `None`; populated by strategy generation tool path |
| `technical_blueprint` | `object` | `computed` | Starts `None`; populated by technical blueprint tool path |
| `strategy_ready` | `boolean` | `state` | Initialized to `false`; later flipped by strategy tool flow |
| `download_complete` | `boolean` | `state` | Initialized to `false`; updated by UI trigger from `generate_and_download` at `response_key=download_accepted` |
| `attachments_allow_bundling` | `boolean` | `config` | `os.getenv("AGENTGEN_ATTACHMENTS_ALLOW_BUNDLING")`, fallback `false`, boolean coercion |
| `chat_attachments` | `array` | `data_reference` | Mongo lookup in `MozaiksAI.ChatSessions` with query `{app_id: {{runtime.app_id}}, _id: {{runtime.chat_id}}}`, field `attachments` |
| `is_multi_workflow` | `boolean` | `computed` | Starts `false`; populated by pattern-selection logic |
| `pack_name` | `string` | `computed` | Starts `null`; populated by pattern-selection logic |
| `current_workflow_index` | `integer` | `state` | Initialized to `0`; updated by pack iteration/runtime loop |
| `workflows_spec` | `array` | `computed` | Starts `[]`; populated from pattern-selection output |
| `macro_workflow_graph` | `object` | `file` | Loads JSON from repo-relative `workflows/extended_orchestration/extension_registry.json`, which resolves to the shared generation-core registry in this repo; fallback `null` |
| `generated_workflows` | `array` | `state` | Initialized to `[]`; appended during pack generation |
| `pack_generation_complete` | `boolean` | `state` | Initialized to `false`; set by pack loop lifecycle path (for example `pack_loop_check`) |

## Runtime-Injected Schema Capability Keys

These keys are available in context even when not declared in `definitions`:

- `database_schema_available` (`boolean`)
- `database_schema_db` (`string | null`)
- `schema_overview` (`string`, when schema loading is enabled)
- `collections_first_docs_full` (`object`, when schema loading is enabled)

They are injected by `core/workflow/context/variables.py` when `CONTEXT_INCLUDE_SCHEMA=true` and a schema DB is configured.

## Local Testing Without Seeded DB Data

When your `data_reference` values (for example `concept_overview`) do not exist yet, runtime can load explicit fallback values from a JSON file.

Environment variable:

- `MOZAIKS_CONTEXT_FALLBACKS_FILE`

Example:

```env
MOZAIKS_CONTEXT_FALLBACKS_FILE=
```

Fallback file shape:

```json
{
  "global": {
    "some_data_reference_var": "fallback value"
  },
  "workflows": {
    "AgentGenerator": {
      "concept_overview": "fallback overview",
      "concept_api_endpoints": [],
      "concept_blueprint": {}
    }
  }
}
```

Resolution order for `data_reference` variables when DB result is `None`:

1. workflow-scoped fallback (`workflows.<workflow_name>.<variable>`)
2. global fallback (`global.<variable>`)
3. convenience flat root object (`<variable>`) when no `global/workflows` wrapper exists

## Agent Exposure

`agents` is used to define which variables are exposed to each agent for context injection. Keep this minimal per agent to reduce token load and accidental coupling.

## Minimal Valid Example

```yaml
definitions: {}
agents:
  GreeterAgent:
    variables: []
```
