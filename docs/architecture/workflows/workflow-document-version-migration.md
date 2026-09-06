# Workflow document version migration

This migration starts from exact main
`5ff00cb1c040d694632e2ec530678c4e9571dc0d` (tree
`dd750e01833fb127061d085fc2f718a081d8266c`), after the structured-output authority
change in PR #485. It versions two existing public document contracts:

| Document | Required exact `schema_version` | Public model |
|---|---|---|
| `orchestrator.yaml` | `mozaiks.orchestrator.v1` | `OrchestratorConfig` |
| `structured_outputs.yaml` | `mozaiks.structured_outputs.v1` | `StructuredOutputsConfig` |

These names follow the repository's `mozaiks.<contract>.vN` vocabulary, including
underscore-separated names such as `mozaiks.runtime_extensions.v1` and
`mozaiks.structured_output_contract_ref.v2`. No competing document-version name
was present. Each field is a required `Literal`, with no default or alias.
Both public parsers serialize and retain it. Missing, null, unknown, misspelled,
numeric, and whitespace-altered versions reject. A valid version does not permit
unknown extra keys. Removing the field after parsing fails cold validation.

## Governed document census

`STATIC_REPOSITORY_DOCUMENT`: **15 orchestrators and 15 structured-output
documents**, 30 files total. Every directory below contains both
`orchestrator.yaml` and `structured_outputs.yaml`; both files migrate. Each
receives one root version field, with all prior YAML values preserved.

| Directory | Files migrated |
|---|---:|
| `factory_app/workflows/AgentGenerator/` | 2 |
| `factory_app/workflows/AppGenerator/` | 2 |
| `factory_app/workflows/AppReview/` | 2 |
| `factory_app/workflows/BrandAssetGeneratorWorkflow/` | 2 |
| `factory_app/workflows/DesignDocs/` | 2 |
| `factory_app/workflows/ExistingAppDiscovery/` | 2 |
| `factory_app/workflows/RuntimeContextExpressionTaskBatchSmoke/` | 2 |
| `factory_app/workflows/RuntimeSmoke/` | 2 |
| `factory_app/workflows/RuntimeTaskBatchSmoke/` | 2 |
| `factory_app/workflows/RuntimeToolCallSmoke/` | 2 |
| `factory_app/workflows/RuntimeUIPrimitiveSmoke/` | 2 |
| `factory_app/workflows/SubscriptionContractDesigner/` | 2 |
| `factory_app/workflows/ThemeCapture/` | 2 |
| `factory_app/workflows/ValueEngine/` | 2 |
| `examples/canonical-apps/research-ops/workflows/ResearchWorkflow/` | 2 |

There are no additional tracked same-name YAML documents intentionally excluded
from this static census. No standalone governed YAML file exists under `tests/`.
The exact paths and pre-migration source/parser fingerprints are captured in
`tests/fixtures/workflow-document-version-migration.json`.

## Producer census

`GENERATOR` surfaces updated:

| Producer | Migration |
|---|---|
| `mozaiks_cli/commands/init.py::_create_starter_workflow` | Emits both exact versions in starter YAML |
| `factory_app/workflows/AgentGenerator/agents.yaml` | Bundle authoring instructions, required-file list, checklist, and inline orchestrator example require versions |
| `factory_app/build_context/AppGenerator/workflow_archetypes.yaml` | All five `orchestrator_defaults` prompt inputs include the version |
| `scripts/smoke_agentgenerator_live_pack.py::_workflow_generation_prompt` | Live generation prompt requires both versions |
| `factory_app/workflows/AgentGenerator/tools/workflow_quality_gate.py::validate_workflow_bundle_structure` | Validates both inner YAML documents through existing public parsers before assembly; injects nothing |

Pass-through consumers preserve the supplied version and need no rewriting:
AgentGenerator `generate_and_download::_write_bundle_to_disk`,
`workflow_converter::promote_generated_workflow`, and CLI generation
`_stage_workflow`, `_adapt_orchestrator`, and `_adapt_structured_outputs`.
WorkflowManager already validates both contracts. Structured-output compilation
and admission already validate `StructuredOutputsConfig`; model construction
consumes only `models` and `registry`. The offline projection's existing
orchestrator consumer now validates the version using `OrchestratorConfig`'s
literal annotation without adding metadata to semantic topology.

`TEST_FIXTURE`: 21 existing fixture producers/helpers migrate 27 orchestrator
and 13 structured-output authoring sites:

```text
tests/slice_5b_helpers.py
tests/test_agentgenerator_generate_and_download_collection.py
tests/test_agentgenerator_generated_workflow_e2e.py
tests/test_ai_research_workspace_golden_path.py
tests/test_brownfield_agentgenerator_acceptance.py
tests/test_declarative_contracts_helpers.py
tests/test_generated_app_archetype_matrix.py
tests/test_generated_app_functional_acceptance.py
tests/test_lifecycle_manager_contract.py
tests/test_pack_config_paths.py
tests/test_semantic_offline_projection.py
tests/test_smoke_agentgenerator_live_pack.py
tests/test_structured_output_cache_invalidation.py
tests/test_structured_output_canonical_identity.py
tests/test_structured_output_provider_boundary.py
tests/test_studio_artifact_restore.py
tests/test_studio_host_smoke.py
tests/test_workflow_declarative_contracts.py
tests/test_workflow_integration_metadata.py
tests/test_workflow_manager_a2a_config.py
tests/test_workflow_manager_reinitialize_identity.py
```

`test_cli_init_prompt.py` additionally verifies both produced headers. New version
and identity tests exercise strict rejection, retained parser output, cold
validation, child-reference comparison, deterministic identity, and exact-base
preservation. The previous #485 identity proof restores only version metadata
for comparison against its unchanged historical capture; it does not parse an
unversioned workflow document.

`DOC_EXAMPLE`: full examples and authoring requirements migrate in:

```text
docs/architecture/workflows/workflow-authoring-contracts.md
docs/architecture/workflows/proposal-only-workflow-pattern.md
docs/architecture/workflows/declarative-ag2-mapping.md
docs/architecture/workflows/structured-output-extraction-contract.md
docs/guides/adding-workflows/01-overview.md
docs/architecture/mozaiksai/auto-tool-execution.md
docs/architecture/builder/agentgenerator-output-assembly-contract.md
.agents/skills/create-workflow/SKILL.md
.claude/skills/create-workflow/SKILL.md
mozaiks_cli/agent_guidance/rules/workflows.md
CLAUDE.md
```

The registry-only auto-tool snippet is explicitly a fragment. Event-contract
trigger snippets and event metadata are also partial or different contracts;
they do not acquire workflow document versions. Outer bundle models and generated
output-model declarations retain their existing fields and schemas.

`DEAD_LEGACY`: `scripts/validate_pattern_examples.py` targets absent
`docs/pattern_examples` and a retired `OrchestratorAgent` shape. It is not an
active producer of these documents and is excluded. AgentGenerator
`WorkflowStrategy` planning examples, integration metadata, provider pricing
registries, and runtime smoke result records are different contracts.
`smoke_factory_artifact_lineage.py` and `smoke_appgenerator_live_acceptance.py`
do not author either governed workflow document. Filename-only markers in
discovery, copy, and CLI staging tests remain deliberately incomplete because
they never reach public document parsing. Historical JSON identity captures and
the arbitrary-JSON extraction fixture are evidence, not active workflow
document producers; they remain unchanged.

## Identity classification and permanent evidence

`EXPECTED_DOCUMENT_VERSION_MIGRATION` covers all 30 source YAML documents and
their parsed document identities, the executable fixture's structured-output
configuration fingerprint, and its enclosing immutable plan-authority input
document and serialized bytes. Removing only the new metadata for comparison
recovers every captured prior source/parser/configuration/authority fingerprint.
Raw byte authority still distinguishes formatting; semantic parse identity is
stable across key ordering, repeated parsing, and process restart.

The existing `tests.slice_5c_revision_helpers.revision_fixture` also pins the
whole authority document. Comparing it on the exact base and migrated tree
changes only `compilation_plan_authority_ref.authority_digest` and the enclosing
genesis `revision_digest` (including its reference). This is also
`EXPECTED_DOCUMENT_VERSION_MIGRATION`; the plan, binding, graph, ledger, evidence,
assignments/results, and all six artifact entries and exact bodies remain equal.
For `tests.test_artifact_revision_store._child_fixture`, the same expected
migration propagates through the parent revision reference, ledger base revision
and digest, evidence ledger reference and digest, and child revision references
and digest. These are the exhaustive child ledger/evidence/revision identity
changes. Its six artifact addresses, content digests, exact bytes, and bundle
digest remain unchanged. No ArtifactRevision contract or evidence design changes.

There is **no changed compiler unit** in the captured current corpus (61/61),
no change to its aggregate plan or graph, and no change to any payload digest.
The six-unit executable fixture's base/successor plans, selected #485 reference,
assignments, and artifact result also retain their identities. The #483 workflow
interface's full identity and rendered bytes remain exact. Output-model fields,
canonical acceptance schemas, and provider response schemas receive no document
version field. Historical 59-unit, #481 taxonomy, #482 retirement, #484 action,
and #485 migration evidence remains unchanged. Any difference beyond the listed
document metadata is `UNRELATED_DRIFT` and fails the comparison tests.

The new evidence lives in `test_workflow_document_versions.py` and
`test_workflow_document_identity_migration.py`. Existing workflow-manager,
generator, offline-projection, canonical identity, plan authority,
materialization, rematerialization, and ArtifactRevision suites remain required.

## App Zero downstream census

`EXTERNAL_DOWNSTREAM`: read-only inspection of `BlocUnited-LLC/mozaiks-app`
found **11 orchestrators and 10 structured-output documents**, all unversioned.
Its inspected OSS pin is `14a8615e52c81f451bc31dd0fb0059f50675753c`.
Every directory below contains both filenames except the indicated single file:

```text
workflows/AppMarketing/
workflows/AssuranceReviewWorkflow/
workflows/CampaignAssetGeneratorWorkflow/       # orchestrator.yaml only
workflows/DomainMigrationWorkflow/
workflows/HostedDeploymentOrchestrator/
workflows/InfrastructureRemediationWorkflow/
workflows/InvestorMarketplace/
workflows/RevenueDistributionReviewWorkflow/
workflows/RevenueParticipationDesignerWorkflow/
workflows/RuntimeSmoke/
workflows/SubscriptionPlanDesigner/
```

When App Zero advances its pinned Mozaiks version to include this change, all
governed documents must add the same public schema versions. This is an
intentional contract migration with no parser compatibility escape hatch.
This OSS change edits no App Zero files.

Content-resolved implementation-artifact selection remains a separate follow-up.
This migration adds no selection resolver, handler source proof, binding
version, approval policy, ArtifactRevision redesign, or runtime result delivery.
