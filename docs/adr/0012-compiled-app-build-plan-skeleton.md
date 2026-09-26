# ADR 0012: Compile the AppBuildPlan Skeleton from Approved Inputs

Date: 2026-09-26

Status: proposed

## Decision

Compile AppBuildPlan's structural fields in the existing Factory plan owner
from pinned approved artifacts, explicit realization decisions, and versioned
construction rules; let the model propose only unresolved, typed judgment
fields keyed to that inventory. The four existing artifact families alone do
**not** determine the entire skeleton today, so missing decisions must be
resolved before construction rather than guessed by either a planner or a
repair.

This is a proposal for maintainer acceptance, not implementation authorization.
This document changes no workflow, model, runtime, or persisted artifact.

### Authority and issue #411

This ADR **adopts with modifications** the direction in
[issue #411](https://github.com/BlocUnited-LLC/mozaiks/issues/411): application
meaning precedes execution planning; deterministic code owns identities,
references, paths, and plan construction. It **defers a new typed decision
ledger**. If introduced later, a ledger must explain decisions against exact
approved revisions, never independently override application facts.

[ADR 0007](0007-generalized-semantic-compiler.md) is Accepted and already names
the intended semantic authority: the pinned SemanticGraph and its typed
payloads. Its generalized compiler exists in
`mozaiksai/core/semantics/compilation_plan.py`, `plan_authority.py`, and
`materialization.py`; `mozaiksai/core/workflow/plan_assignment_compiler.py`
projects its assignments. These paths explicitly remain offline. The active
Factory still reviews and caches AppBuildPlan through
`AppGenerator/tools/app_plan_review.py` and `app_build_plan.py`.

If accepted, this ADR **modifies ADR 0007's pre-cutover sequencing**: retire
model authorship of the operational AppBuildPlan skeleton before the full
SemanticGraph authority/persistence cutover. It does not activate the offline
compiler, declare its missing inputs solved, or introduce a second semantic
graph, renderer registry, scheduler, or general compiler. Extend the current
Factory plan constructor and reuse canonical layout, task, pack, and validation
owners. The operational input remains the approved artifact revision set;
AppBuildPlan becomes its derived execution projection, with bounded content
resolved before approval, not another editable semantic root.

At ADR 0007's eventual authority cutover, retire this artifact-to-AppBuildPlan
projection with AppBuildPlan itself; replace it with the existing graph-derived
plan/assignment path in one cutover. Do not retain both source paths or a
fallback to model-authored plans. ADR 0008 is still Proposed and is not a
prerequisite authorization. Preserve the target binding of
[ADR 0009](0009-factory-execution-and-build-target-identity.md) and the immutable
approved task/recovery boundary of
[ADR 0011](0011-factory-bounded-task-recovery.md).

### Proposed construction boundary

1. Resolve one owner/target/build-scoped revision set: approved DesignDocs
   surface map and experience specification, approved subscription contract,
   selected pack contracts and versions/digests, plus explicit realization
   decisions. Add the verified baseline and approved change scope for revision
   or adoption. An available pack catalog is not proof of pack selection.
2. Resolve and approve every inventory-affecting semantic choice, then validate
   agreement and reference closure. Missing input, an ambiguous binding, or
   incompatible data-contract shapes produces feedback to its owning semantic
   decision step. Never choose a module by label similarity.
3. Construct a closed inventory and typed judgment slots. The model can fill
   operation implementation semantics, entity-field detail, page-section detail,
   and task explanation within that inventory. Preserve already approved values.
   Content cannot add an action, integration, output family, or dependency. A
   candidate needing such a change returns to step 2 for a successor approval;
   the constructor never discovers new inventory from arbitrary content.
4. Derive structural fields once. Render immutable contract references and
   operation IDs into worker instructions separately from model-authored
   explanatory content. Validate and freeze the resulting plan before caching
   the task-batch projection.
5. Execute, assemble, and recover against that exact plan and input revision.
   A changed input requires a newly approved plan, never silent recompilation
   under an existing execution result.

The resolution step extends the existing typed artifact/selection contracts;
it is not a free-form decision dictionary or a second independently current
"resolved model." Each fact has one owning artifact. Compilation provenance
records its immutable reference and digest, not another editable copy.

Pre-inventory approval and post-inventory content filling are separate steps.
DesignDocs' surface contract should own app-local realization choices such as
`implementation_mode`, `user_data_scope`, optional companions/helpers/extensions,
and semantic dependencies; its experience contract owns page realization and
action bindings, and its data contract owns entity/persistence decisions.
Selected pack and subscription contracts retain their existing provider/facade
and subscription authority. Verified revision/adoption inputs own baseline and
change scope. These are proposed extensions of those owners, not fields already
present today. Migration step 1 must settle the exact typed fields, producer,
approval point, and immutable artifact reference for each missing greenfield
decision before step 2 can centralize its construction rule. Revision/adoption
baseline decisions close in their later enablement slices, before those modes
are admitted. No free-form ledger supplies missing facts behind that boundary.

### Construction rules to make explicit

These are proposed deterministic policies, not facts that all four upstream
artifacts already state:

- App-owned approved module surfaces produce generated modules unless an
  explicitly selected registered implementation supplies them. Platform-owned
  auth mechanics produce no app module. Hosted/managed implementations retain
  their declared client/facade boundary. Category hints never become pack IDs.
- A generated module has capability ID equal to its approved surface ID.
  Registered packs use their registered IDs; facades use contract module IDs,
  separately from the provider capability.
- Use one task per resolved surface/task-type lane, with IDs derived from that
  pair; any necessary partition requires an explicit stable partition key.
  Worker selection comes from the canonical task-type mapping. Generation
  order is a stable topological projection, not advisory model prose.
- The ordinary generated-module profile owns `module.yaml`,
  `backend/schemas.py`, and `backend/handler.py`/`service.py` through the
  existing contract/model/service trio. Owned entities additionally require
  `repo.py`/`policy.py`; explicit `user_data_scope` selects
  `account_data_handler.py`. Optional companions, helpers, callbacks, pollers,
  custom UI, and migrations require typed declarations before paths exist.
- A page's canonical path uses the existing route-derived materializer.
  Use one aggregate page-bundle lane for the selected page scope, with one
  owner of `app.json` when that file is selected. Revisions do not automatically
  select every page or `app.json`. Template-owned and worker-owned outputs
  must have one explicit producer per path.
- Models depend on their module contract; services additionally depend on
  schemas; applicable persistence tasks precede persistent module code.
  Facades depend on their explicitly bound integration client. For now, pages
  receive all selected module contracts as conservative direct input visibility.
  That last rule is a documented policy, not knowledge of every eventual
  page-to-action binding. Validate the global DAG and exclusive ownership.
- `subscription_contract.contract_required` selects the
  `subscription_config` lane and `config/subscriptions.yaml`; copy the approved
  payload. Monetization alone does not imply subscriptions or select a provider.
  Resolve contradictory/missing provider selection before construction.
- Do not derive integration ownership from a client filename, select an adapter
  by prose matching, or create a read action from an entity's spelling.

## Reason

### Evidence and limits

Reviewed at OSS `ca5c1a5c9cbfa14a9387210af5c706f4a34bfc1d`. The operator reports
five monetized traversals ending at plan review, with changing fields rather
than one stable defect. The latest complete live payload and five run IDs were
not supplied; this ADR does not claim to have replayed all five runs.

| Observation | Repository evidence and limit |
| --- | --- |
| Provider and facade collapsed | [#731](https://github.com/BlocUnited-LLC/mozaiks/pull/731) repairs `mozaikspay` versus `billing_portal` from the pack contract. |
| `task_id = task_type` collided across modules | [#732](https://github.com/BlocUnited-LLC/mozaiks/pull/732) qualifies identities and rewrites references. |
| Approved `tasks` paths carried `task_registry` or `task_management` labels | [#733](https://github.com/BlocUnited-LLC/mozaiks/pull/733) repairs agreement-proven labels before synthesis and guards existing owners. |
| Latest reported `user_auth`, kind `app_policy`, category `billing_pack` | ValueEngine's prompt explicitly describes platform auth as requiring no generation. This does not prohibit app-owned auth policy/configuration; it prohibits inferring a generated auth module from platform mechanics. |
| Approved IDs and presence of auth vary upstream | DesignDocs IDs are model-authored strings with naming guidance; saving them does not generically derive IDs from a stable concept key. Pack facade IDs are the narrower deterministic exception. |

A controlled, read-only replay at this base used the real
`ContextVariablesBridge` returned by `_reported_plan('tasks')` in
[the monetized regression fixture](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/tests/test_app_plan_module_task_capability_repair.py).
It added an unapproved but internally consistent `user_auth` capability and
module-contract/model/service trio owning `modules/user_auth/` paths, leaving
the approved surface map unchanged.
With `surface_kind=app_policy`, `validate_plan_origins` passed but public review
returned `needs_revision`: that kind permits only `subscription_config`.
With `surface_kind=module`, origins passed and public review returned
`ready` with 13 tasks. This is a synthetic counterexample, not the exact latest
live plan. It shows why fixing the newest label or merely retaining the current
origin validator is not a closed-inventory design.

Primary source anchors:

- [Planner schemas at the reviewed base](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/AppGenerator/structured_outputs.yaml):
  `AppBuildPlan`, `AppCapabilityPack`, `AppBuildTask`, `AppBuildPage`.
- [Review and every repair](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/AppGenerator/tools/app_plan_review.py)
  and [subsequent normalization/cache](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/AppGenerator/tools/app_build_plan.py).
- [DesignDocs schemas](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/DesignDocs/structured_outputs.yaml)
  and [save boundary](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/DesignDocs/tools/save_design_doc.py).
- [File contracts](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/build_context/AppGenerator/file_contracts.yaml),
  [managed pack contract](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/build_context/mozaikspay/contract.yaml),
  and [ValueEngine auth guidance](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/ValueEngine/agents.yaml).

### Field inventory

Classification: **A** copies an approved fact; **C** is a deterministic
construction rule after inputs close; **J** requires explicit semantic or
product judgment; **E** comes from verified runtime, integration, or baseline
evidence. "A when present" never authorizes inventing an absent value.
Combined classifications identify a current missing input, not competing
writers. Values already approved are preserved; subsequent judgment requires
an approved successor of their owning artifact.

Every top-level `AppBuildPlan` field at the reviewed base is listed below.

| Field | Class and authority |
| --- | --- |
| `agent_message` | J: non-authoritative summary. |
| `app_kind` | A/J/E: approved product classification or adoption classification; not derivable from module names. |
| `pages` | A/C/J: complete retained inventory; field breakdown below. |
| `entities` | A identities from surfaces/data contract; explicit operations A, missing operations and explanatory notes J. |
| `roles` | A approved role policy; otherwise J, not inferred from "auth" labels. |
| `auth_strategy` | A approved auth behavior or J unresolved policy; never permission to invent an auth module. |
| `service_scope` | C projection of resolved backend realization inventory. |
| `frontend_scope` | C projection of resolved page/customization inventory. |
| `theme_preferences` | A intake preference, null when absent; no invented preference. |
| `brand_intent` | A upstream brand direction; `style_summary`, `brand_keywords`, `experience_goals`, `appearance_hint` need J only if unresolved. |
| `shell_preset_hint` | A selected preset or J catalog choice; C documented default when no preference exists. |
| `profile_layout` | A/J product layout choice; C explicit default, not a fact from surface membership. |
| `surface_map` | A approved surface inventory; added semantic dependencies require J as detailed below. |
| `capability_packs` | C expansion of resolved A selections, with remaining J fields below. Available packs alone do not authorize selection. |
| `readiness_profile` | A/J explicit readiness choice or C documented classification rule; existing prose inference is not approved input. |
| `revenue_model` | A resolved monetization choice; do not reclassify from the task list. |
| `monetization_plan` | A resolved route/contract/provider decisions; remaining explanatory/product fields below. |
| `monetization_provider` | A explicit resolved provider selection; absent/contradictory selection is a gap, not a filename/default guess. |
| `external_integrations` | A selected pack contract or J approved direct integration definition. |
| `workspace_integration_status` | E actual catalog check result; neither compiler nor model can fabricate configured credentials. |
| `event_flows` | A declared event identities/owners; J unresolved producing actions and subscriber relationships. |
| `workflow_touchpoints` | A/E registered workflow and page identities; J placement, launch binding, and supplied context. |
| `demo_fixture_sets` | J bounded demo data design using validated references; never production data authority. |
| `agent_backend_required` | A/E approved workflow/backend integration requirement and generated backend context. |
| `build_tasks` | C from closed realization and change scope; per-field rules below. |
| `data_contract` | A approved data semantics, subject to current schema reconciliation gap below; J unresolved data/alias decisions. |
| `pending_schema_migration` | J approved additive change against E baseline; C identifiers/paths after approval. |
| `deployment_targets` | A explicit provider-neutral launch contract, which is outside the four proposed inputs; otherwise C empty list. |
| `deployment_template_manifest` | C export renderer output/E validation evidence; null before that output exists. Not model-authored execution evidence. |
| `carry_forward_decisions` | J approved reuse/adapt/regenerate/drop decision against E verified prior modules; C affected task references. |
| `generation_order` | C stable DAG order; retire model-authored advisory phase labels as execution input. |

#### Surface, capability, and task fields

| `surface_map.surfaces[]` field | Class and authority |
| --- | --- |
| `surface_id`, `surface_kind`, `owner` | A exact approved DesignSurface identity/classification. |
| `source_capability_packs` | A preserved hints; resolving a hint to a registered implementation needs an explicit selected binding. |
| `label`, `primary_entities`, `owned_pages`, `owned_mutations`, `events_emitted`, `workflow_triggers` | A approved declarations; trigger intent is not yet a verified executable workflow reference. |
| `summary` | A projection of DesignSurface `notes`, or C descriptive template. |
| `dependencies` | J resolved relationships: absent from DesignSurface today; C expansion once approved. |

| `capability_packs[]` field | Class and authority |
| --- | --- |
| `capability_pack_id` | C generated module = approved surface ID; A registered pack ID or facade `module_id`. |
| `surface_id`, `surface_kind` | A approved mapping; C separate provider/facade realization from explicit pack contract. |
| `pack_type` | A explicit product category or J taxonomy selection; not identity or authority. |
| `label`, `summary` | A surface/pack description or C neutral projection; no new semantics. |
| `implementation_mode` | A selected implementation or J unresolved realization; DesignSurface does not declare it. |
| `primary_entities` | A approved surface ownership, not provider internals. |
| `user_data_scope` | J privacy/data-lifecycle decision unless approved elsewhere; absent from DesignSurface and affects required files. |
| `primary_pages` | A approved owned pages and pack facade pages after inventory agreement. |
| `operations` | A approved mutation/action IDs; J missing reads and other semantics. Never infer a complete action contract from entity names. |
| `required_integrations` | A pack declarations or J approved direct integration contract. |
| `agentic_extensions` | A resolved workflow references or J unresolved concept intent; cannot manufacture a generated workflow. |
| `capability_source` | A registered descriptor or C explicit owner/realization mapping; unresolved reuse/provider selection needs J. |

| `build_tasks[]` field | Class and authority |
| --- | --- |
| `task_id` | C stable surface/task-type/explicit-partition identity. |
| `task_type` | C closed task taxonomy selected by resolved realization and mode. |
| `capability_pack_id`, `surface_id`, `surface_kind` | C references to the closed inventory; no writable model labels. |
| `execution_target`, `initial_agent` | C canonical task-type worker mapping, not independent model selections. |
| `description` | C neutral summary or J explanatory text only. |
| `initial_message` | C immutable bindings, paths, and required action IDs plus J bounded task content; content cannot override ownership. |
| `owned_paths` | C mandatory profiles, page materializer, and explicitly selected optional outputs. Not fully derivable from today's surface map. |
| `depends_on` | C required producer visibility, explicit semantic edges, and stable DAG construction; arbitrary model edges are not accepted. |
| `acceptance_criteria` | C contract checks plus J approved product outcomes. |
| `context_variables` | C typed task-local projections/default empty list; any J override must be approved and cannot shadow protected context. `key`, `value`, `value_type` remain typed. |
| `integration_needs` | C selected requirements and exact consumer references from approved integration definitions. |
| `domain_context` | C serialization of the same resolved facts; cannot hide alternate identity, topology, or capabilities in JSON text. |

#### Page and semantic detail fields

| Field | Class and authority |
| --- | --- |
| `pages[].name`, `route` | A exact ExperienceSpec inventory, including retained pages in revisions. |
| `pages[].purpose`, `design_intent` | A approved page intent; only unresolved detail is J. |
| `pages[].primary_entities`, `primary_actions` | A explicit bindings when supplied; otherwise J validated module/action binding. |
| `pages[].ui_layout` | A ExperienceSpec layout. |
| `pages[].shell_mode_hint`, `page_type_hint` | A pack/approved choice or J bounded selection; not generally present in ExperienceSpec. |
| `pages[].ui_surface` | J approved declarative/custom realization under framework constraints; absent from ExperienceSpec. |
| `pages[].sections_hint[].primitive`, `section_id_hint`, `intent`, `config_hint` | A approved section primitive/ID/intent/hint. Partial hint completion is J; illustrative endpoint strings are not validated executable bindings. |
| `pages[].sections_hint[].title_hint` | A explicit approved title or J copy. |
| `entities[].name`, `operations`, `notes` | A entity ID and explicit operations; J missing operations/notes. Entity field definitions live in the data contract, not this three-field model. |
| `monetization_plan.monetized`, `revenue_model`, `subscription_contract_requirement`, `monetization_provider`, `required_capability_routes` | A resolved concept/subscription/provider decisions; C consistent projection after resolution. |
| `monetization_plan.primary_payer`, `primary_payee`, `money_flow_summary`, `rationale` | A explicit concept decisions or J unresolved business meaning/explanation. |
| `external_integrations[]` fields `name`, `service`, `display_name`, `kind`, `purpose`, `required_at`, `optional`, `preferred_setup_lane`, `allowed_setup_lanes`, `managed_default`, `required_fields` | A selected pack/catalog contract; otherwise J vetted direct integration. External `kind` categorizes services such as payments/email. Capability requirements omit `name`, add `provider`, and use connector kinds (`api_key`, `oauth`, `webhook`, `managed_capability`, `internal_service`). Task needs also use connector kinds, omit `name`/`provider`, and add C `required_by.kind/id/path`. |
| Integration `required_fields[].name/label/type/required/frontend_safe/options` | A catalog definition or J reviewed typed definition; names/metadata only, no credentials. |
| `workspace_integration_status[].catalog_id/display_name/workspace_status/missing_secrets` | E catalog check; `auto_wire` is C from verified configured status. |
| `event_flows[].event_type/producer_pack_id` | A declared event and owner; `producing_action/subscriber_intents/workflow_capability_ids` are A explicit edges or J checked relationship choices. |
| `workflow_touchpoints[].page_name/workflow_id` | A/E exact page and workflow; `section_id_hint/action_id/label/placement/context_variables/purpose` are A approved bindings or J UI launch decisions. |
| `demo_fixture_sets[].fixture_id/purpose/scope/routes/record_groups/cleanup_strategy/notes` | J approved fixture design; C stable fixture IDs and checked target references. Groups' `module_id/entity_name/route_hint/record_role/record_count/required_states/stable_keys/notes` remain bounded fixture data. |
| `carry_forward_decisions[].module_id` | E prior module identity; `decision/reason/source` J with verified human/proposal provenance; `affected_build_tasks` C after compilation. |

Data-contract ownership is not permission to redesign approved entity fields.
`data_contract.version` is a C schema convention;
`surfaces[].surface_id/surface_kind/collections` is A data ownership.
Collection `name/scope/ownership.surface_id/ownership.surface_kind`,
`fields[].name/type/required`, `indexes[].name/keys[].field/keys[].order/unique/sparse`,
and `lifecycle.write_mode/migration_policy` copy approved values. Missing
domain choices are J before approval, not post-compilation overrides.
`aliases[].alias/collection/owner_module/access/description` requires an explicit
approved alias/pack declaration. `shared_collections` requires lossless shape
reconciliation.

The current DesignDocs data schema includes field `default/enum/nullable`,
collection `search_by`, and bundle `app_id/artifact_version_id/policies` that
AppGenerator's plan DataContract does not represent. DesignDocs shared
collections are objects; the plan lists names. Plan aliases have no equivalent
DesignDocs field. Moreover, the plan schema/file catalog allow opt-in
`data_contract=null`, while the planner prompt demands collections for durable
persistence. **A compiler must not silently drop these facts or choose between
contradictory persistence requirements.** Reconcile the existing canonical data
contracts and explicit persistence selection before migrating that input.

For `pending_schema_migration`, `migration_id/version/summary/operations` come
from an approved additive baseline diff; construction owns identity/path.
Operation `type/module_id/entity_name/field_name/field_type/required/index/description`
and index `name/keys[].field/keys[].order/unique` are approved semantic changes,
not facts recoverable from a surface name.

Deployment is outside the proposed four-input derivation.

| Deployment field | Class and authority |
| --- | --- |
| `deployment_targets[]`: `target_id`, `target_kind`, `artifact_outputs`, `deployment_profile` | A explicit launch selection or C renderer convention; not inferred from modules. |
| Target `runtime`: `container_port`, `health_path`, `start_command` | A runtime launch contract. |
| Target `environment`: `required_variables`, `optional_variables`, `secret_variables`, `public_variables` | A names-only launch contract. |
| Target `image`: `image_name`, `tag_strategy`; `checks`: `build`, `smoke`, `health` | A launch/build policy or C explicit renderer defaults. |
| Target `provider_profile`: `name`, `description`, `metadata[].key`, `metadata[].value` | A selected provider-neutral profile and bounded metadata. |
| Target/manifest `ci_secret_requirements`: `required`, `optional`, `workflow_inputs` | A selected CI contract or C renderer requirements. Secret entries carry `name`, `purpose`, `used_by`; input entries carry `name`, `required`, `purpose`. Names and purposes only, never secret values. |
| `deployment_template_manifest`: `schema_version`, `app_id`, `deployment_profile`, `generated_files`, `required_env`, `secret_env`, `exposed_ports` | C renderer projections from the selected launch contract. |
| Manifest `healthcheck.path`, `healthcheck.port`, `ci_workflow`, `dockerfile`, `compose`, `deploy_target_spec` | C concrete renderer projections. |
| Manifest `validation_status`, `build_output_contract` | E actual validation/build results. |
| Build output `repo_url`, `commit_sha`, `build_status`, `image_ref`, `artifact_digest`, `workflow_run_url`, `logs_url`, `safe_details[].key`, `safe_details[].value`, `error_code` | E actual export/build evidence; planning cannot fabricate it. |

Deployment files remain export-owned, outside build task paths.

### What disproves an unconditional "compile everything now"

The same approved surface can legally require different optional manifests,
helpers, runtime extensions, data-account hooks, clients, or custom UI; those
choices change `owned_paths`, `task_type`, and `depends_on`. Current
DesignSurface has no typed fields for several of these decisions.
ExperienceSpec also does not supply every page-to-action relationship.

The missing-read repair invents `list_<first entity>`; that is a semantic
choice, not merely restoring an approved operation. The page dependency repair
deliberately supplies every module contract because actual binding is not yet
known. Revision/adoption additionally needs scope and baseline evidence absent
from the four artifacts. The existing generalized plan authority explicitly
reports `BASE_AUTHORITY_MISSING` for unsupported baseline reuse/preservation;
it cannot be switched into production by wiring one function.

Thus the **identity and mandatory-profile premise is verified**; the **complete
four-input sufficiency premise is disproved**. Adding missing typed decisions
and explicit policies is a prerequisite, not an implementation detail to bury
inside a compiler.

## Alternatives Considered

- **Keep adding repairs.** Cheapest isolated fix and useful when a value is
  already uniquely determined, as #733 demonstrates. Rejected as the steady
  state: the model continues to author a competing skeleton, repair order
  changes outcomes, and semantic guesses accumulate beside deterministic rules.
- **Strengthen prompts and retry full plans.** Preserves flexible decomposition
  and costs little schema work. Rejected for fixed identities/ownership:
  retries re-author facts and consume the same bounded budget. Useful only for
  genuine unresolved judgment.
- **Compile directly from the four current artifacts without extra decisions.**
  Attractive simplicity; rejected by the field-level counterexamples above.
  Hidden defaults would be unreviewed product decisions.
- **Drop every unapproved capability after generation.** Could unblock this
  particular auth error. Rejected as the lasting contract: silent deletion can
  discard requested behavior and leave dependent tasks or prose inconsistent.
- **Use a whitelist validator and keep model-authored structure.** Safer than
  today's incomplete allowlist and worthwhile at the input boundary, but still
  makes the model guess the one valid structure. Validation remains necessary;
  it is not the producer.
- **Make a new semantic model and decision ledger for this repair.** Rejected:
  ADR 0007 already owns the semantic compiler direction. A second fact store
  or general compiler would split authority again.
- **Wait for the complete ADR 0007 cutover.** Avoids a transitional projection
  and is a reasonable maintainer alternative if its remaining gaps can close
  promptly. This proposal instead makes a bounded change to the active Factory
  owner while preserving that destination. Rejecting this ADR in favor of
  waiting is preferable to approving two production authority paths.
- **Canonicalize names from concept prose globally.** Rejected: equivalent
  prose can admit different module decomposition; slugging or fuzzy matching
  cannot prove semantic identity.

## Consequences

### Repair-lane disposition

The structural repair chain is retired at the producer cutover. Its valid
rules become construction/validation rules in the sole constructor, not a
second pass over a model-owned skeleton.

| Current `app_plan_review.py` repair | Disposition |
| --- | --- |
| `_repair_task_identities` | C IDs assigned before model content; delete collision qualification/reference rewriting. |
| `_repair_module_task_capabilities` | C inventory references; delete label correction. Surface/path disagreement rejects at construction/validation. |
| `_repair_plan` | C approved capability expansion and entity projection; delete alias renaming, path relocation, guessed source correction, and drop propagation. |
| `_repair_coverage` | C closed task/file/page construction and exclusive ownership; delete omission patching, synthesized duplicate candidates, and unapproved-page dropping. No blanket genesis expansion in other modes. |
| `_repair_module_task_dependencies` | C canonical prerequisite edges from selected producers, once. |
| `_repair_missing_read_operation` | Retire heuristic. Missing operation semantics returns J feedback before compilation; approved reads become typed action facts. |
| `_repair_page_contract_dependencies` | C explicit conservative visibility rule until approved bindings permit narrower edges. |
| `_repair_managed_facade_capabilities` | C separate selected provider/facade construction; delete consolidation/retargeting of model-invented identities. |
| `_repair_subscription_config_task` | C explicit `contract_required` selection; do not reconstruct missing approval from monetization prose. |
| `_repair_contract_task_operations` | C render approved typed action IDs into worker context; delete searching/patching prose as action authority. |

Retirement also includes post-review mutations in `app_build_plan.py`:
`_normalize_build_task_identity`, persistence-task merging/ID redirection,
provider inference/defaulting, adapter-filename identity inference, capability
insertion from available rather than selected packs, facade/page insertion,
endpoint rewriting, and dependency inference (including prose/sole-adapter
fallbacks). Retain their legitimate registry, template, and projection
mechanisms under one construction owner. `app_build_plan` validates the frozen
result and projects/caches it; it must not silently change its inventory.

Bounded feedback for invalid **judgment candidates** remains before plan
approval. Worker output validation and ADR 0011 artifact/task recovery remain
afterward. Neither is a replacement structural repair lane. Delete obsolete
model fields, prompts, context projections, normalizers, fixtures, and repair
tests together under the
[pre-production replacement policy](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/docs/agent-engineering-contract.md#pre-production-replacement-policy).
Replace tests with construction, closure, and rejection proofs; no compatibility
aliases, feature-flag fallback, or previous full-plan input path survives cutover.

### Invented auth and other unapproved surfaces

An executable `user_auth` module is **impossible by construction** when no
approved realization owns it: judgment output has no writable module/capability
inventory, IDs, task types, paths, or dependency list. Attempts to supply those
fields or unknown structured references receive **rejection with bounded
feedback**, not silent dropping. Explanatory prose cannot create executable
bindings; it is not possible to guarantee detection of every semantic request
hidden in prose. Worker outputs still require validation against the approved
inventory, rejecting unauthorized artifacts/actions there. New product scope
must return to the owning approval step.

An approved platform-auth requirement may still produce provider-neutral
`config/auth.yaml` or existing host configuration through its declared owner.
It does not generate local auth internals. If upstream itself incorrectly
approves an app-owned auth module, fix that classification before compiling;
the compiler cannot erase approved intent using prompt prose as higher authority.

### Upstream identity drift

Pin `surface_id`, owner, and kind when a design is first accepted, and reuse
them for unchanged surfaces across retries, handoffs, and revisions of that
app lineage. Add/rename/remove/reclassify is an explicit approved change with
reference updates. Pack-owned facade identities remain contract-defined.
Owner/kind changes cannot be disguised as label changes.

Do **not** promise the same IDs or decomposition for independent new apps
described by the same text. Neither the concept nor DesignDocs currently
provides a stable cross-run semantic key. A concept-derived identity allocator
requires its own typed identity/decomposition decision and is deferred.
Within-lineage pinning and explicit change detection are prerequisites here;
a global prose-to-ID naming algorithm is out of scope.

### Consumers and modes

This table describes the eventual consumer contract. Initial mode availability
and the later enablement gates are specified below; consumer coverage does not
require enabling every mode at the first producer cutover.

| Consumer | Required change and preserved boundary |
| --- | --- |
| `AppGenerator/agents.yaml`, `structured_outputs.yaml`, `tools.yaml`, `context_variables.yaml`, middleware and transition bindings | Replace full-plan model authoring with keyed judgment candidates and the sole construction/review tool. Preserve bounded failure routing; no success on unresolved input. |
| `app_build_plan.py` and `hydrate_app_build_plan_context.py` | One validate/project/cache path. Preloaded plans currently can bypass review; close that entry, or accept only a verified exact approved plan for resume. |
| `extended_orchestration/task_batches.yaml`, runtime `task_batches.py` | Consume the same task schema, worker mapping, paths and direct dependencies. AG2 owns execution; no new scheduler. |
| `assemble_app_tasks.py` and pack materialization | Use compiled page/file ownership and selected templates. Preserve unchanged baseline files and retained task results; assembly summary remains separate evidence. |
| `task_integrity.py`, `repair_policy.py` and task recovery | Inventory and plan/input digests must match. Recover original task IDs, dependencies, ownership, outputs and budgets; never recompile during recovery. |
| Refinement/revision: `hydrate_app_revision_context.py`, artifact-repair and carry-forward paths | Full resulting architecture plus selected task delta. Require owner/target-verified immutable baseline, approved change scope, deletions and explicit reuse/adapt/regenerate/drop choices. Do not rebuild unaffected modules. |
| Brownfield discovery/adoption and overlay | Require adopted contract inventory and preservation/adaptation decisions. Do not create greenfield trios for externally owned surfaces. The deterministic `ExistingAppDiscovery/tools/app_build_plan_handoff.py` helper exists, but only test callers were found; it is not evidence of a production compiler entrypoint. Fold its rules into the same owner or remove it. |

A compiler does not remove the need for reasoning; it makes the approval
boundary explicit and prevents repeated re-authoring of settled facts.
Generated runtime contracts, security enforcement, provider isolation, and
promotion gates remain intact. Changes to planner structured-output contracts,
artifact input provenance, and resume compatibility are real migration costs.

### Greenfield export before brownfield: one producer, staged availability

Choose **option (a)**: greenfield genesis reaches verified export before
brownfield implementation is admitted. Replace the producer once for the entire
Factory, while initially admitting only fresh greenfield genesis and recovery
within its compiled, frozen plan. Revision and adoption remain explicitly
unavailable until the same constructor supports their approved inputs. This
supersedes the all-mode parity prerequisite for step 3; it accepts a temporary
reduction in mode availability, not a claim that those modes already work.

The no-dual-path rule is unchanged. At cutover, delete the previous full-plan
authoring schemas, prompts, tools, repairs and alternate handoff producers for
**every** mode, including modes still blocked. No dormant producer, mode switch
to the previous planner, historical-plan conversion, or fallback is retained.
An unsupported request stops with an explicit blocking result before planning,
task dispatch or writes. Later mode support adds input closure and selection
rules to this same constructor; it does not restore a second author.

| Request at the initial cutover | Admission and required evidence |
| --- | --- |
| Fresh greenfield genesis, free or monetized | Admit after approved input closure. Require plan review, task execution, assembly, existing acceptance gates, canonical artifact persistence and verified export. |
| Retry, repair or restart within that new genesis build | Admit only for the exact frozen plan, input revisions, constructor contract and original AG2 channel, retaining successful outputs and consumed budgets. Prove task recovery and owned artifact repair without recompiling the plan. |
| Refinement/revision, including an existing lineage's full rebuild | Block until revision support is verified. A new build ID or `build_mode=initial` does not turn an existing lineage into fresh greenfield genesis. |
| Brownfield discovery handoff, overlay or module generation | Block generation until adoption support is verified, even when it would create the app's first canonical artifact lineage. Read-only discovery may continue. |
| Pre-cutover, foreign, unproven or stale plan/task cache and resume | Block execution. A self-consistent inventory or digest is insufficient; never stamp new construction provenance onto an old plan. |
| Existing exported apps and read-only artifact inspection | Continue under their existing contracts; this restriction concerns Factory generation and mutation. |

Admission must use trusted owner/target and lineage evidence, including
`run_build_binding.phase` and adoption provenance, rather than a model-supplied
mode label. Brownfield can itself be genesis, so the phase alone is insufficient.
Same-plan worker repair can use a `needs_revision` quality outcome without
becoming a new refinement plan. Conversely, changed requirements or ownership,
including a post-export change request, cannot be relabeled as recovery to get
through the greenfield gate.

Enforce one Factory admission decision through the existing authority seams at
all execution and write entrances: app-producing launch/refinement routes;
review and construction; preloaded-plan hydration, including cached task items;
batch dispatch/recovery and direct repair entry; save, assembly, acceptance and
export; and both cold resume before pending-turn replay and live continuation.
Current [before-chat lifecycle errors are logged and execution continues](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/mozaiksai/core/workflow/orchestration_patterns.py#L801),
and [plan hydration can skip when task items already exist](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/factory_app/workflows/AppGenerator/tools/hydrate_app_build_plan_context.py#L33).
Raising only in a hydration hook therefore does not establish this boundary.
The implementation must prove rejection before execution or writes through
every bypass, without adding a parallel scheduler or mode-routing subsystem.

Drain or stop affected in-flight Factory runs before cutover and replace their
loaded workflow/tool contracts together. Do not leave old in-memory runners or
mixed producer versions serving other modes. Old saved artifacts remain
inspectable, but old executions do not resume under the new contract. Rollback
reverts the complete deployment; it never runs both producers together.

The **initial acceptance/export gate** is a live monetized greenfield build
through exported artifact, a free greenfield counterpart, same-plan failure and
restart recovery, and rejection proofs for every unavailable entrypoint. Use
the exact OSS/product commit pair and record plan/input and export digests.
Passing review alone does not satisfy this gate. Brownfield success is not a
prerequisite. After greenfield export passes, revision and then adoption gain
their own live enablement gates. Until each passes, its admission remains
blocked. Full mode parity is required to claim those modes supported, not to
accept the first greenfield export; this does not authorize a public release.

### Migration and live verification order

No implementation or live model spend is authorized by this proposed ADR.
Each future implementation slice needs offline evidence first, then a fresh
authorized live traversal against the exact OSS/product commit pair. Persist
input revisions, plan digest, queued task identities, review outcome and
acceptance evidence. Never count an old cached plan as proof of the new path.

| Step | Bounded change and deletion boundary | Live verification |
| --- | --- | --- |
| 1. Close and pin greenfield inputs | Establish exact approved artifact/pack reads, within-lineage ID preservation, auth disposition, and unresolved realization choices for greenfield. Reconcile its data-contract projection. Identify trusted lineage/adoption evidence needed to reject other modes; defer their baseline compilation semantics. Keep the existing planner as the sole producer in this preparatory slice. | Repeat a monetized design through handoff/reload and verify identical accepted identities. Missing facts yield specific feedback. This step alone does not remove the current planner's ability to invent `user_auth`. |
| 2. Centralize construction rules | Extract mandatory task/path/worker/dependency/pack rules into canonical catalogs/helpers used by the incumbent path only. Prove greenfield construction and explicit optional outputs; prepare its frozen-plan consumers, recovery and export, plus rejection coverage for deferred modes. No shadow production compiler or new semantic store; do not activate the offline generalized compiler. | Run the same monetized case and compare selected identities/owners at the existing review boundary. A live rejection can still occur; this proves rule extraction without claiming the incident fixed. |
| 3. Replace the producer atomically; export greenfield | Switch model output to keyed judgments and construct AppBuildPlan once. Delete all previous structural repair/normalization/full-plan producers for every mode. Update admission, review, hydration, task projection, assembly, recovery and export together. Retain the internal result shape, not its model-authoring contract. Admit only the greenfield cases above; deferred modes block with no fallback. | **First step expected to pass review for the reported monetized traversal**, given complete approved inputs and judgments: exactly approved `tasks`, selected provider and `billing_portal`, no `user_auth`, unique path ownership, subscription task and DAG. Prove invalid-candidate rejection, then valid `ready`. Continue through the initial acceptance/export gate above, including free genesis and same-plan recovery; brownfield is not a prerequisite. |
| 4. Enable revision/refinement | After greenfield export, add verified baseline/change-scope inputs and delta selection to the same constructor. Require preserved ownership, explicit deletions and carry-forward decisions. Enable these routes only when their live acceptance passes; adoption stays blocked. Verify step 3 already removed obsolete paths, fixtures and guidance. | Make a scoped revision of the exported greenfield app, preserve unaffected files and successful outputs, exercise revision recovery, and export the accepted result. Re-run greenfield acceptance for shared contract changes. |
| 5. Enable brownfield | Add adopted inventory, external ownership and preservation/adaptation decisions to the same constructor. Enable adoption routes only after their input and live acceptance gates pass. No previous handoff producer or fallback returns. | Discover an existing app, approve its adoption scope, generate only selected owned outputs, preserve external files, recover and export. Record this evidence separately from earlier greenfield success. |

Steps 1 and 2 may be split into greenfield data and integration contract PRs
when needed, each with one source of authority per field. Baseline compilation
belongs to steps 4 and 5 and does not delay greenfield export. Step 3 is the smallest
safe producer flip: splitting its schema/prompt/entrypoint deletion across
deployments would leave two authors. Its admission matrix makes unavailable
modes explicit; it does not quietly route them to another author. Broader
SemanticGraph authority/persistence migration remains
the separately governed ADR 0007 cutover.

"Pass plan review" does not promise successful generated code, authentication,
billing calls, persistence, deployment, or end-to-end acceptance. The latest
saved live plan must be captured during implementation to verify that the
reported inventory assumptions actually hold.

## Reversibility

**Medium risk:** the generated app file contracts can remain stable, but
planner input contracts, approved plan provenance, and resumable execution
identity require migration. Before producer cutover, changes are local
contract/refactoring steps. After cutover, rollback is deployment rollback to
the prior coherent producer; affected new runs must restart or stay blocked.
Do not dual-read old plans or reinterpret existing task evidence under new
identities to make rollback appear seamless.

## Affected Invariants

From [Architectural Invariants](https://github.com/BlocUnited-LLC/mozaiks/blob/ca5c1a5c/ARCHITECTURAL_INVARIANTS.md):

- **1 — Provider-neutral public contracts:** managed clients/facades and host
  auth remain separate from provider internals.
- **2 — No raw secrets in generated artifacts:** integration and deployment
  planning contains names/references only.
- **3 — Agents produce candidates; deterministic code validates and promotes:**
  extends deterministic ownership to execution-plan construction.
- **4 — Classified/versioned contracts:** judgment output and provenance changes
  require explicit contract migration; approved revisions remain addressable.
- **6 — Authority bypass must not expand:** input pinning does not authorize a
  caller, configure credentials, or bypass module auth.
- **7 — Public-contract dogfooding:** OSS and hosted product traversal evidence
  must use the same planning boundary.
- **8 — Operator capabilities stay separate:** no hosted business authority,
  cross-customer decision policy, or paid infrastructure is added.

## OSS Boundary

**Keep OSS: foundational.** Deterministic construction, closure checks, typed
provenance, and baseline-aware plan projection belong with existing public
Factory contracts. Product-specific selection intelligence remains behind
those contracts. AG2 owns model calls, agents, execution and recovery mechanics;
this proposal adds no agent framework, proprietary provider implementation,
cloud provisioning, or release action.

## Validation

Before accepting this ADR: review the field inventory against the cited base,
confirm the ADR 0007 sequencing amendment and the no-ledger boundary, review
all ten repair dispositions and downstream mutations, and agree on input closure,
the temporary mode restrictions and later enablement gates. Acceptance of this
document alone does not prove implementation.

Apply the following checks to each slice's affected contract. Step 3 must prove
the complete initial admission/export matrix; deferred modes must prove blocking
until their own enablement slice:

- Use real `ContextVariablesBridge` tests for copied approval state, closure,
  unknown-field/reference rejection, and exact cache/queue projection.
- Reproduce #731/#732/#733 and the controlled auth counterexample. Authorized
  inputs must yield one provider/facade split and one owner per path; unknown
  surfaces cannot create executable tasks.
- Prove determinism for identical pinned inputs and semantically irrelevant
  ordering, explicit optional-family selection, canonical task IDs, dependency
  visibility, and no post-approval plan mutation.
- Prove incomplete operations, `user_data_scope`, integrations, optional files,
  data-contract conversion and baseline authority fail with explicit gaps.
  Approved field/section/operation semantics must survive without loss.
- Cover all preloaded/resume entrypoints, task retry fingerprints,
  successful-output reuse and stale/foreign input rejection at cutover. Prove
  blocking for revision/adoption, including attempts to relabel them as genesis.
  Before each later mode is enabled, cover retained pages/capabilities,
  baseline ownership, carry-forward and its own recovery and export.
- Verify retired repair, normalizer and full-plan authoring paths are absent;
  run affected contract/acceptance tests, Ruff, mypy and the required full suite.
- Record the live evidence specified for that slice, then generated-app
  functional acceptance. Keep plan review, task completion, artifact acceptance
  and promotion as separate outcomes.

For this documentation PR: check the template sections, complete current field
inventory, source links and ADR status references; build the docs, run applicable
documentation checks, and leave the PR a draft with auto-merge disabled.
