# Dead Code And Legacy Logic Audit

Date: July 13, 2026

Scope: active source, contracts, docs, tests, scripts, and first-party app bundle in `C:\Repos\BlocUnitedRepo\mozaiks`, excluding vendored/runtime output directories such as `.venv`, `node_modules`, `generated`, `logs`, `htmlcov`, `dist`, and build artifacts.

## What Was Checked

- Repository inventory: 1,910 non-vendor files.
- Python static checks: `ruff` for unused imports/undefined names/redefinitions, and `vulture` for high-confidence unused code.
- Pytest collection for full-suite import and contract discovery.
- Targeted tests around AppGenerator assembly and AG2 orchestration.
- Text scans for legacy/deprecated/obsolete/shim/unused/stub/demo/placeholder terminology.
- Route and registry alignment for Studio/admin pages.
- Provider terminology scan for standalone Stripe references.

## Cleaned Immediately

These were local, high-confidence dead-code items with no current contract value:

- Removed unreachable code after `_base_result()` in `factory_app/workflows/AppGenerator/tools/app_validation.py`.
- Removed unused reserved arguments from `assemble_features()` in `factory_app/workflows/AppGenerator/tools/assembly_phase.py`.
- Removed the unused local `pattern_num` callback parameter in `mozaiks_cli/commands/gen.py`.
- Removed the unused `transition_graph_factory` parameter from `run_workflow_orchestration()` in `mozaiksai/core/workflow/orchestration_patterns.py`.
- Removed active demo `messages` and `contacts` modules from the first-party
  Studio app bundle. The reusable generated-app messaging build context remains
  available under `factory_app/build_context/messaging`.
- Made `factory_app/app/ui/route_manifest.json` the first-party Studio route
  source for app pages, including `/apps/:appId/activity` -> `AppBuildHistoryPage`.
- Reduced `factory_app/app/admin/admin_registry.yaml` to an AdminPortal
  extension-page registry and removed duplicate Studio route declarations.
- Removed hosted/social page registrations and unrouted page files from the OSS
  Studio app bundle.

## Remaining Findings

### 1. Intentional Signature Slots Still Flagged By Vulture

After cleanup, high-confidence vulture findings are signature slots:

- `factory_app/workflows/AgentGenerator/tools/hook_universal_prompts.py`: `run_context`
- `factory_app/workflows/AppGenerator/tools/integration_tests.py`: `agent_context`
- `mozaiksai/core/ports/ssl_provider.py`: `provisioning_id` in protocol methods

Recommendation: keep these unless the hook/tool/protocol call contracts are changed. They are not dead behavior in the same sense as unreachable code.

### 2. Standalone Stripe References

No active standalone `Stripe`/`STRIPE` payment references were found in the checked source paths. The only `stripe` hits came from generated LiteLLM model/provider names such as `pinstripes` inside the usage pricing catalog.

## Intentional Non-Issues

- Tests and smoke scripts contain fixtures, stubs, demo IDs, and placeholder assertions by design.
- Generated app templates include placeholder/legal copy where templates explicitly require user replacement.
- `chat-ui` standalone demo config and workflow stub aliases are development-only package surfaces, not Studio runtime logic.
- No legacy `autogen.beta.*` imports were found in active code paths; those terms remain only in changelog/test hygiene contexts.

## Validation

- `ruff check factory_app\workflows\AppGenerator\tools\app_validation.py factory_app\workflows\AppGenerator\tools\assembly_phase.py mozaiks_cli\commands\gen.py mozaiksai\core\workflow\orchestration_patterns.py`
- `python -m pytest tests\test_appgenerator_canonical_generation.py tests\test_appgenerator_managed_capability_smoke.py tests\test_ag2_network_execution_alignment.py -q --no-cov`
- `python -m pytest --collect-only -q --no-cov`
