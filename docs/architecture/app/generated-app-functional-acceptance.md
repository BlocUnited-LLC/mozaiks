# Generated-App Functional Acceptance

Mozaiks generated apps must be functionally coherent, not only schema-valid.
The acceptance boundary is:

```text
deterministic canonical plan
-> generated app bundle
-> static cross-artifact validation
-> runtime load smoke
-> representative routes/actions/facades resolve
```

This gate is intentionally independent of App Zero and BlocUnited hosted
services. Proprietary strategy may produce better app plans, but OSS baseline
output and proprietary-enhanced output must pass the same canonical acceptance
contract.

## Functional Completeness Definition

A generated app is functionally complete when:

- `ui/route_manifest.json` routes resolve to a built-in page, registered custom
  component, intentional redirect, or explicitly external route.
- `SchemaPage` routes point to a matching `ui/pages/*.yaml` or `*.yml` schema.
- Declared module actions in `modules/*/module.yaml` have an implemented module
  handler method.
- Page and custom UI module calls target declared `/api/modules/{module}/{action}`
  actions.
- Workflow YAML/JSON module-action references target declared module actions.
- Selected managed capabilities include the generated app-facing facade files
  and actions required by their public contract.
- Expected generated app surfaces do not contain accidental 501,
  `NotImplementedError`, or `*_NOT_IMPLEMENTED` placeholders.
- The app bundle loads through `AppLoader` without startup import errors.

The gate does not require real Stripe, Azure, GitHub, DNS, or BlocUnited hosted
credentials. External provider configuration may fail clearly at runtime, but an
internally declared app route/action/facade must not be missing.

## Existing Validation Matrix

| Invariant | Existing Validator | Static / Runtime | Coverage |
| --- | --- | --- | --- |
| Canonical path and secret boundaries | `generated_bundle_scanner.scan_generated_bundle` | Static | Existing, reused |
| Page schema shape and module endpoint syntax | `audit_page_schemas` | Static | Existing, reused |
| Page endpoint to module action wiring | `validate_wiring` | Static | Existing, reused in AppGenerator gate |
| Module action handler implementation | `validate_module_implementation_contract` | Static AST | Existing, reused in AppGenerator gate |
| Placeholder backend runtime data | `audit_module_runtime_quality` | Static AST/text | Existing, reused in AppGenerator gate |
| Workflow event/capability integration | `validate_workflow_integration_contract` | Static | Existing, reused when AgentGenerator metadata exists |
| App startup import/load | `AppLoader.load` via acceptance gate | Runtime smoke | Existing, reused |
| Route component/schema resolution | `scan_functional_generated_app` | Static | Added |
| Workflow module-action target resolution | `scan_functional_generated_app` | Static | Added |
| Managed capability facade completeness | `scan_functional_generated_app` | Static | Added |
| 501/not-implemented generated surfaces | `scan_functional_generated_app` | Static | Added to public facade |

## Diagnostics

Functional failures are structured diagnostics. Examples:

- `MISSING_ROUTE_COMPONENT`
- `MISSING_SCHEMA_PAGE`
- `MISSING_MODULE_ACTION`
- `MISSING_MODULE_HANDLER`
- `CAPABILITY_FACADE_MISSING`
- `PLACEHOLDER_IMPLEMENTATION`

These diagnostics are exposed through
`mozaiksai.core.validation.validate_generated_app_bundle(...)` and through the
AppGenerator `functional_completeness` acceptance subgate.

## Representative Archetypes

Current automated coverage includes:

- Basic authenticated CRUD route/action/schema coherence.
- Monetized SaaS facade expectations for the public MozaiksPay-compatible
  generated-app contract.
- Workflow/agent module-action references through declarative workflow YAML.

The first gate uses deterministic fixtures rather than live LLM calls. This
tests the deterministic boundary after reasoning:

```text
captured/generated artifacts -> functional acceptance
```

## CI Meaning

The functional scanner is part of normal pytest coverage through
`tests/test_generated_app_functional_acceptance.py` and is invoked by the public
generated-app validation facade. A failure means the Factory can produce or
accept an app bundle whose declared app surfaces do not resolve internally.

The gate is deliberately not a broad static analyzer for arbitrary Python or
JavaScript. It validates canonical contracts and generated references where the
framework has deterministic knowledge.

## Remaining Gaps

P0: none identified by this pass in the covered deterministic fixtures.

P1:

- Add a generated artifact fixture that exercises a full deterministic
  AppPlan-to-materialized-app path for monetized SaaS once that fixture can be
  run without paid LLM calls.
- Add browser-level route rendering smoke for generated bundles if CI can run it
  without making ordinary unit feedback slow.

P2:

- Expand workflow reference checks as workflow packs add more declarative target
  shapes.
- Add optional brownfield generated-app acceptance once existing-app discovery
  has a stable deterministic adoption-plan fixture.
