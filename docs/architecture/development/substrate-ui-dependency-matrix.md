# Substrate UI Dependency Matrix

**Status**: Development  
**Purpose**: Field-level derivation source of truth for non-AI CRUD and UI page
planning in the builder pipeline.

## Derivation Schema

Each output variable is documented with:

- **Semantic Upstream Reference**
- **Rule/Taxonomy**
- **How to Obtain (Pattern-Aware)**

## 1. SubstrateScopeAgent

**Role**: normalize non-AI scope from intent  
**Output**: substrate scope brief (feeds `DecompositionPackage`)

### Upstream Dependencies

- `IntentBrief`
- `CapabilityMap.capabilities[]`
- `PlatformProvisionPlan.provisions[]`

### Derivation Logic

- **`bounded_contexts`**
  - Semantic Upstream Reference: `IntentBrief.bounded_contexts`,
    `CapabilityMap.capabilities[].summary`
  - Rule/Taxonomy: collapse duplicate product areas into stable bounded contexts.
  - How to Obtain (Pattern-Aware):
    1. Collect all context hints from intent and capability summaries.
    2. Merge aliases into one canonical context name.
    3. Keep names domain-first, not workflow-first.
- **`primary_non_ai_capabilities`**
  - Semantic Upstream Reference: `CapabilityMap.capabilities[]`
  - Rule/Taxonomy: select capabilities with `primary_surface` in `module|action`.
  - How to Obtain (Pattern-Aware):
    1. Filter out workflow-primary items.
    2. Keep deterministic, auditable product functions.
    3. Mark each for entity/view/action decomposition.

## 2. EntityModelAgent

**Role**: derive durable data objects  
**Output Model**: `EntitySpec[]`

### Upstream Dependencies

- `IntentBrief.business_entities`
- `CapabilityMap.capabilities[].entity_candidates`
- `SubstrateScopeAgent.bounded_contexts`

### Derivation Logic

- **`EntitySpec.name`**
  - Semantic Upstream Reference: business entity candidates
  - Rule/Taxonomy: singular, domain-meaningful nouns.
  - How to Obtain (Pattern-Aware):
    1. Normalize entity candidate strings.
    2. Remove UI and workflow words.
    3. Emit canonical entity names.
- **`EntitySpec.key_fields[]`**
  - Semantic Upstream Reference: capability action inputs and display needs.
  - Rule/Taxonomy: only fields needed for persistence, filtering, and policy.
  - How to Obtain (Pattern-Aware):
    1. Gather required inputs from action candidates.
    2. Add fields required for list/detail page views.
    3. Mark required fields conservatively.
- **`EntitySpec.relations[]`**
  - Semantic Upstream Reference: cross-entity language in intent/capabilities.
  - Rule/Taxonomy: explicit relation declarations only when used by actions/views.
  - How to Obtain (Pattern-Aware):
    1. Detect owner-child or reference patterns.
    2. Keep relation labels consistent across entities.

## 3. CrudActionAgent

**Role**: derive deterministic app actions  
**Output Model**: `ActionSpec[]`

### Upstream Dependencies

- `CapabilityMap.capabilities[].action_candidates`
- `EntitySpec[]`
- `PolicySpec[]` (if already drafted)

### Derivation Logic

- **`ActionSpec.name`**
  - Semantic Upstream Reference: action candidates + capability labels.
  - Rule/Taxonomy: verb-object naming (`create_lead`, `archive_show`).
  - How to Obtain (Pattern-Aware):
    1. Convert user verbs into deterministic action names.
    2. Remove ambiguous "assist/help" verbs from CRUD actions.
- **`ActionSpec.reads` / `writes`**
  - Semantic Upstream Reference: entity references and mutation semantics.
  - Rule/Taxonomy: list exact entities read and mutated.
  - How to Obtain (Pattern-Aware):
    1. Map action inputs to entity reads.
    2. Map successful mutation targets to writes.
    3. Keep writes empty for read-only actions.
- **`ActionSpec.required_inputs[]`**
  - Semantic Upstream Reference: entity required fields + capability constraints.
  - Rule/Taxonomy: include only runtime-required input keys.
  - How to Obtain (Pattern-Aware):
    1. Start from target entity required fields.
    2. Add policy-dependent keys only if runtime must provide them.

## 4. DomainEventAgent

**Role**: map deterministic actions to domain events  
**Output Model**: `DomainEventSpec[]`

### Upstream Dependencies

- `ActionSpec[]`
- `Capability.automation_route_refs`
- event boundary rules from foundations docs

### Derivation Logic

- **`DomainEventSpec.event_type`**
  - Semantic Upstream Reference: action meaning, not workflow names.
  - Rule/Taxonomy: lowercase dot notation.
  - How to Obtain (Pattern-Aware):
    1. Translate action outcomes to business facts (`lead.created`).
    2. Reject any workflow-id tokens in event names.
- **`DomainEventSpec.source_event`**
  - Semantic Upstream Reference: substrate emitter names.
  - Rule/Taxonomy: snake_case internal event identifier.
  - How to Obtain (Pattern-Aware):
    1. Convert action or manager event names to snake_case.
    2. Keep stable names for bridge compatibility.
- **`DomainEventSpec.post_commit_only`**
  - Semantic Upstream Reference: action mutation semantics.
  - Rule/Taxonomy: default true.
  - How to Obtain (Pattern-Aware):
    1. If event represents committed mutation, set true.
    2. Set false only for pre-commit/no-commit advisory signals.

## 5. AutomationRouteAgent

**Role**: define policy from event to effect  
**Output Model**: `AutomationRouteSpec[]`

### Upstream Dependencies

- `DomainEventSpec[]`
- `WorkflowSpec[]`
- capability automation requirements

### Derivation Logic

- **`AutomationRouteSpec.route_id`**
  - Semantic Upstream Reference: event + target workflow intent.
  - Rule/Taxonomy: stable kebab-case identifier.
  - How to Obtain (Pattern-Aware):
    1. Combine event intent and effect target.
    2. Ensure uniqueness across the app.
- **`AutomationRouteSpec.event_type`**
  - Semantic Upstream Reference: `DomainEventSpec.event_type`
  - Rule/Taxonomy: must reference declared catalog event.
  - How to Obtain (Pattern-Aware):
    1. Select exact event type from domain event list.
    2. Never invent undeclared events at route stage.
- **`AutomationRouteSpec.effect`**
  - Semantic Upstream Reference: automation need + workflow inventory.
  - Rule/Taxonomy: starter mode uses only `workflow.run` or `workflow.resume`.
  - How to Obtain (Pattern-Aware):
    1. Choose run vs resume based on chat continuity requirement.
    2. Bind to an existing workflow name only.
- **`AutomationRouteSpec.bindings`**
  - Semantic Upstream Reference: event envelope tenant fields.
  - Rule/Taxonomy: always bind `app_id` and `user_id`; bind `chat_id` when resume is needed.
  - How to Obtain (Pattern-Aware):
    1. Start with `tenant.app_id`, `tenant.user_id`.
    2. Add `tenant.chat_id` for resume flows.

## 6. UiPageAgent

**Role**: derive page and module navigation projections  
**Output**: shell and module projections

### Upstream Dependencies

- `ModuleSpec[]`
- `ViewSpec[]`
- `CapabilityMap.capabilities[]` with `primary_surface=module`

### Derivation Logic

- **`navigation_config.pages[]` projection**
  - Semantic Upstream Reference: module routes that must appear in shell discover/header.
  - Rule/Taxonomy: keep shell pages as projection metadata, not business logic.
  - How to Obtain (Pattern-Aware):
    1. Project module route + label + component for shell display.
    2. Keep semantic header controls separate from pages.
- **`module_registry.modules[]` projection**
  - Semantic Upstream Reference: `ModuleSpec`
  - Rule/Taxonomy: one registry entry per enabled module.
  - How to Obtain (Pattern-Aware):
    1. Emit module metadata (`name`, `display_name`, `path`, `component`, `backend`).
    2. Ensure module names match folder names and stub files.

## 7. StubContractAgent

**Role**: generate strict code stubs where declaratives are insufficient  
**Output**: minimal deterministic stubs

### Upstream Dependencies

- `ModuleSpec[]`
- `ActionSpec[]`
- `BundlePlan.module_paths`

### Derivation Logic

- **`platform/modules/<name>/handler.py`**
  - Semantic Upstream Reference: module actions + required integrations.
  - Rule/Taxonomy: strict async execute signature.
  - How to Obtain (Pattern-Aware):
    1. Emit `async def execute(data: dict) -> dict`.
    2. Add action dispatch branches only for declared actions.
    3. Return deterministic, JSON-serializable payloads.
- **`platform/modules/<name>/ui/index.js`**
  - Semantic Upstream Reference: module page component names.
  - Rule/Taxonomy: default export object map.
  - How to Obtain (Pattern-Aware):
    1. Import declared page component(s).
    2. Export stable key map used by registry/component loader.

## Validation Gates

Before build compile:

1. every `AutomationRouteSpec.event_type` exists in `DomainEventSpec`
2. every enabled workflow effect points to declared `WorkflowSpec`
3. every module route has matching registry and navigation projection
4. every generated stub follows strict signature contracts
5. no substrate spec field embeds workflow names except route effects

