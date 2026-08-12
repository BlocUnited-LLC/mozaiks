# Stability and Compatibility

Mozaiks is pre-1.0. Breaking changes can still happen, but they follow a
deliberate process. This page explains the three stability tiers and how
changes are introduced so that self-hosters can plan accordingly.

---

## Three-Tier Model

### Tier 1 — Stable Public Seam

Intentionally public, covered by `test_public_framework_contract_freeze.py`
and the self-host acceptance suite. Stable within a pre-1.0 minor version.
Breaking changes require a CHANGELOG entry and a version bump.

**What is in Tier 1:**
- Module dispatch public API: `dispatch_module_action`, `ModuleActionDispatchRequest`, `ModuleDispatchScope`, `ModuleDispatchMetadata`, `ModuleDispatchAuthority`, `ModuleDispatchProvenance`, `ModuleExecutionPolicyInput`, `ModuleExecutionPolicyDecision`, `ModuleDispatchAudit`, `ModuleEventProvenance`, `ModuleReactionProvenance`, `ModuleReactionAudit`
- Platform extension contract: `PlatformExtensionBundle`, `PLATFORM_EXTENSION_SCHEMA_VERSION`
- Validation facade: `validate_generated_app_bundle`, `GeneratedAppValidationRequest`, `GeneratedAppValidationResult`, `GeneratedAppValidationDiagnostic`
- Studio scope: `StudioScope`, `resolve_studio_scope`
- App loading: `AppLoader`, `AppLoadResult`, `AppDefinition`
- Entitlement contract: `EntitlementPort`, `ConfiguredEntitlementAdapter`, `NoOpEntitlementAdapter`
- Port contracts: `AppBackendPort`, `OrchestrationPort`, `SandboxPort`
- Auth adapter protocol: `AuthAdapter`, `UserClaims`, `AuthError`
- `mozaiks_cli` entry point and documented subcommands
- All YAML contract schemas with declared `schema:` fields (`mozaiks.*.v1`)
- Package version constant: `mozaiksai.__version__`

### Tier 2 — Experimental

Public-facing but subject to change without a deprecation window. These
surfaces are working but the interface shape may still move.

**What is in Tier 2:**
- `mozaiks gen` CLI subcommand (prompt-to-app convenience; interface may simplify)
- `mozaiks context index` (app intelligence indexing; pipeline still evolving)
- `mozaiks sync-agent-guidance` (contributor tooling; format may change)
- `mozaiks migrations status` (diagnostic; schema may change)
- `mozaiks quickstart` and `mozaiks onboard` (onboarding paths; UX still being refined)
- `ConnectorVaultBackend` and all vault backend implementations (interface stable, adapters may add params)
- Telemetry and observability hooks (`AG2_OTEL_*`, `INSIGHTS_*`)
- `RUNTIME_PLATFORM_EXTENSIONS` injection mechanism (hook names stable; new hooks may be added)

### Tier 3 — Internal

Not public API. May change or disappear without notice. Do not import directly.

**What is in Tier 3:**
- Everything under `mozaiksai.core.workflow.internal`
- `ExecutorRegistry` and `ExecutorType` (internal dispatch machinery)
- `ModuleLoader` internals beyond the `AppLoader` façade
- Source hygiene scan functions in `scripts/production_readiness_gate.py`
- All symbols not in `__all__` of a public package

---

## How We Introduce Breaking Changes (Pre-1.0)

Pre-1.0 semantics: the minor version (`0.x`) may include breaking changes.
Patch versions (`0.x.y`) are additive only.

For Tier 1 interfaces:
1. Breaking change lands in a minor bump (e.g. `0.1.11` → `0.2.0`).
2. The CHANGELOG entry uses `Changed` or `Dropped` and names the affected symbol.
3. If the replacement exists, the old path is documented in the CHANGELOG explicitly, not silently dropped.

For Tier 2 interfaces:
- May change in patch or minor bumps.
- CHANGELOG entry is provided but no deprecation window is promised.

For Tier 3 internals:
- No notice required.

---

## OSS Boundary Stability

The OSS/proprietary boundary is frozen.
See [ADR 0003](../adr/0003-pre-1-0-oss-proprietary-boundary-freeze.md) and
the [OSS Boundary Family Registry](../architecture/foundations/oss-boundary-families.md).

The DO-NOT-MOVE families listed in the registry will not be moved to a private
repo before 1.0. If a family is moved post-1.0, a new ADR records the decision
first.

---

## What Self-Hosters Can Count On Today

- `pip install -e ".[dev]"` from a clean clone works without private config.
- `mozaiks serve .` boots the Studio host without BlocUnited infrastructure.
- The clean-room self-host acceptance suite (`tests/test_selfhost_clean_install.py`) passes on every main commit.
- No runtime source imports from `mozaiks_app` (the private hosted-product repo).
- No BlocUnited domains hardcoded in runtime or factory workflow source.
- The package content guard blocks OSS wheel builds that accidentally include private artifacts.

---

## Version Policy

Current version: see `mozaiksai.__version__`.

The package is pre-1.0 and not yet published to PyPI. Install from a local
editable checkout. See [self-hosting guide](self-hosting.md) and
[releasing.md](../releasing.md) for the current publication status.

Do not depend on a specific pre-1.0 `0.x.y` version as a stable API target.
Pin to a specific git SHA for reproducible builds until 1.0.
