# AppGenerator Prompt/Context Debt Inventory

Companion to [ADR 0008](../../adr/0008-deterministic-engineering-context.md).
This is an actionable inventory of evidence-backed findings, not normative
architecture: it records what exists today so future slices can retire it
under the ADR's cleanup discipline. Classification:

- **P0** — architecture/correctness: violates a stated invariant or creates
  a governance gap.
- **P1** — quality/cost: measurable waste or degradation risk.
- **P2** — cleanup: dead or duplicated mechanism with no behavior at stake.

Line references are from the audit at main `e2c1ec0d`; verify before acting.

| # | Finding | Evidence | Class |
|---|---|---|---|
| A | Whole generated-bundle file map (`generated_files`) rendered by `str()` into four agents' system messages on repair re-entry | `factory_app/workflows/AppGenerator/tools/assemble_app_tasks.py:411-415`; `mozaiksai/core/workflow/context/context_utils.py:148` | P0 |
| B | `code_files` + `deleted_files` whole maps exposed to AssemblyAgent | `factory_app/workflows/AppGenerator/context_variables.yaml` agent block | P0 |
| C | The compact-context invariant in `ARCHITECTURE.md` is enforced only by a string-presence test, not behavior | `tests/test_app_context_architecture_contract.py:93` | P0 |
| D | Coding agents receive everything eagerly yet have no bounded retrieval; the fix must route through the consolidated `core/app_context` authority (local machinery removal is test-locked) | `factory_app/workflows/AppGenerator/tools.yaml`; `tests/test_graph_authority_contract.py:69-72` | P0 |
| E | `stringify_context_value` is bare `str()` with no size cap anywhere in the exposure path | `mozaiksai/core/workflow/context/context_utils.py:69-83,148` | P0 |
| F | Exposure `mapping` is built from all context variables, so a declared `template` could reference unexposed keys (latent, unexploited) | `context_utils.py:137-146` | P0 |
| G | Assignment landing zones (`dependency_context_refs`, `allowed_agent_ids`, validator/output ids) are free-form and unpopulated; Slice 5 must fill them with manifest-compatible grammars or later migration is forced | `mozaiksai/core/workflow/plan_assignment_compiler.py` | P0 |
| H | `[OUTPUT FORMAT]` prose restates registered response schemas (ServiceAgent section alone ~7,280 chars) | `agents.yaml`; `structured_outputs.yaml` | P1 |
| I | 26 fenced JSON/YAML shape blocks in prompts alongside 218 structured-output models; classify per ADR 0008 (semantic guidance → skills; shape duplication → delete) | `agents.yaml` | P1 |
| J | File-contract facts delivered up to three times per agent (prompt prose + hook injection + schema) | `factory_app/workflows/AppGenerator/tools/hook_file_contract_context.py:337-383` | P1 |
| K | Language-profile hook duplicated across six agents | `middleware.yaml:76-93` | P1 |
| L | Subscription contract triple-delivered (two variables + one hook) to the same agents | `middleware.yaml:52-60`; `context_variables.yaml` | P1 |
| M | 35 prompt-middleware entries re-derive injections on every LLM call | `middleware.yaml:1-106`; `mozaiksai/core/workflow/execution/middleware.py:29-96` | P1 |
| N | Module-archetype hook falls through to rendering the whole 45KB catalog when no archetype resolves | `hook_file_contract_context.py:238-292` | P1 |
| O | AppPlanAgent static prompt ~87,662 chars (~21.9k tokens) | `agents.yaml` | P1 |
| P | AppSchemaAgent single `[CONTEXT]` section of ~30,070 chars | `agents.yaml` | P1 |
| Q | Seven overlapping intelligence-snapshot variables exposed to two agents for one snapshot | `context_variables.yaml:1441-1450,1494-1503` | P1 |
| R | No token budget or compaction handed to AG2; watch-only alerts never truncate or block | `mozaiksai/core/usage/watchdog.py:205-264` | P1 |
| S | Context values frozen into system messages at agent construction, so values go stale across a run | `factory.py:547-554` | P1 |
| T | Two divergent `_compose_prompt_sections` implementations | `factory.py:271` vs `mozaiksai/core/workflow/context/projection.py:29` | P2 |
| U | Older dict-shaped `prompt_sections` normalization path retained beside the list shape | `factory.py:271-300` | P2 |
| V | `prompt_sections_custom` / `system_message` fallback chain with unverified consumers | `factory.py:540-544` | P2 |
| W | `context_graph_pack` reachable only via hook while absent from every exposure list (asymmetric mechanism) | `_shared/context_graph/hook_context_graph.py`; `context_variables.yaml` | P2 |
| X | `read_artifact_file` permits up to 80,000 characters per read, ungoverned by any grant | `factory_app/refinement_harness/config/tools.yaml:106-112` | P1 |
| Y | `subscription_contract` and `subscription_contract_artifact` are two variables for one fact | `context_variables.yaml` | P2 |
| Z | Write-only exposure entries with no production reader (e.g. `app_plan_ready`) | `context_variables.yaml:939-944`; test-only readers | P2 |

Ordering note: P0 items G and A–F gate or shape Slice 5 and the post-Slice-5
context slice; P1 items are candidates for ordinary maintenance under the
ADR's deletion discipline; P2 items ride along with whichever change touches
their file.
