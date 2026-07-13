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

## Remaining Findings

### 1. Active Stub/Demo Modules In The First-Party App Bundle

`factory_app/app/modules/messages` and `factory_app/app/modules/contacts` are active modules, not test fixtures. They are explicitly described as stubs and return hardcoded demo data:

- `factory_app/app/modules/messages/module.yaml`
- `factory_app/app/modules/messages/backend/handler.py`
- `factory_app/app/modules/contacts/module.yaml`
- `factory_app/app/modules/contacts/backend/handler.py`
- `factory_app/app/ui/pages/custom/MessagesPage.jsx`
- `factory_app/app/ui/pages/custom/MessageThreadPage.jsx`
- `factory_app/app/ui/components/MessagingTab.jsx`
- `factory_app/app/ui/components/ContactsTab.jsx`

Production recommendation: either promote these into real persisted social modules or remove them from the default Studio app bundle. Keeping active demo modules conflicts with the no-placeholder runtime rule.

### 2. Studio App Routes Are Split Across Two Sources

`factory_app/app/ui/route_manifest.json` declares concrete Studio page components for several app pages, while `factory_app/app/admin/admin_registry.yaml` also declares admin pages for the same area. Because `mozaiksai/hosts/platform.py` dedupes by first path, route-manifest entries win when paths overlap.

Current drift:

- `/apps/:appId/activity` exists in `admin_registry.yaml` but not in `route_manifest.json`.
- `AppBuildHistoryPage` is registered in `factory_app/app/admin/index.js`, and `AppOverviewPage` links to `/apps/:appId/activity`, but the route currently falls through to generic `AdminPortal` rather than the custom build-history page.
- `operations` and `settings` are declared in `admin_registry.yaml` but have no first-party custom route-manifest pages.

Production recommendation: make `route_manifest.json` the first-party Studio page route source and keep `admin_registry.yaml` for AdminPortal/panel pages, or move all app-level Studio pages into AdminPortal. The current hybrid creates unreachable custom components and confusing nav behavior.

### 3. Registered But Unrouted Hosted/Social Pages

Several components are registered in `factory_app/app/admin/index.js` and contain route comments, but no current route manifest or admin registry entry exposes those routes:

- `AppCommunityPage`
- `AppGovernancePage`
- `AppGovernanceProposalPage`
- `AppCollaboratorsPage`
- `AppRevenueParticipationPage`
- `RevenueParticipationPlanReviewPage`
- `RevenueDistributionReviewPage`
- `MyCommunitiesPage`
- `MyInvitationsPage`
- `MyVotesPage`
- `MyDelegationsPage`

Production recommendation: these should move to `mozaiks-app` if they are hosted-product capabilities, or be backed by real OSS modules and routes if they are intended to ship in the first-party Studio bundle. Do not leave them registered but unreachable.

### 4. Intentional Signature Slots Still Flagged By Vulture

After cleanup, high-confidence vulture findings are signature slots:

- `factory_app/workflows/AgentGenerator/tools/hook_universal_prompts.py`: `run_context`
- `factory_app/workflows/AppGenerator/tools/integration_tests.py`: `agent_context`
- `mozaiksai/core/ports/ssl_provider.py`: `provisioning_id` in protocol methods

Recommendation: keep these unless the hook/tool/protocol call contracts are changed. They are not dead behavior in the same sense as unreachable code.

### 5. Standalone Stripe References

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

