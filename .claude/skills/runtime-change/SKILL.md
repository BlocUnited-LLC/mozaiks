---
name: runtime-change
description: Review or implement a change to runtime or platform composition, module loading and execution, auth, transport, persistence, runtime extensions, or route and module dispatch.
argument-hint: "[change summary or file path]"
---

Use this skill when a change touches the OSS runtime or platform host.

Typical triggers:

- `mozaiksai/hosts/runtime.py`
- `mozaiksai/hosts/platform.py`
- `mozaiksai/core/runtime/app/module_loader.py`
- `mozaiksai/core/runtime/composition/module_executor.py`
- `mozaiksai/core/runtime/composition/module_event_router.py`
- `mozaiksai/core/runtime/composition/module_context.py`
- `mozaiksai/core/runtime/composition/extensions.py`
- `mozaiksai/core/auth/**`
- `mozaiksai/core/transport/**`
- `mozaiksai/core/runtime/persistence/**`

Inspect first:

- `ARCHITECTURE.md`
- `AGENTS.md`
- `CLAUDE.md`
- `.claude/rules/runtime.md`
- `.claude/rules/architecture-boundaries.md`
- `.claude/rules/testing.md`
- `docs/architecture/app/platform-authoring.md` when app loading, shell, route ownership, or module dispatch changes
- `docs/architecture/modules-systems/module-system.md` when module contracts or module runtime composition change
- `docs/architecture/foundations/events-and-data/persistence-and-artifact-storage.md` when `ctx.persistence`, data contract, indexes, or migrations change
- `factory_app/build_context/AppGenerator/file_contracts.yaml` when generated app or module contracts depend on the runtime behavior
- the owning runtime file and its narrowest test slice before editing:
  - `mozaiksai/hosts/runtime.py` + `tests/test_runtime_websocket_contract.py` or `tests/test_auth_oidc_discovery.py`
  - `mozaiksai/hosts/platform.py` + `tests/test_platform_ai_config_resolution.py`, `tests/test_platform_layout.py`, or `tests/test_platform_shell_p0_fixes.py`
  - `mozaiksai/core/runtime/app/module_loader.py` + `tests/test_module_loader_contracts.py`
  - `mozaiksai/core/runtime/composition/extensions.py` + `tests/test_module_runtime_extensions.py`
  - `mozaiksai/core/runtime/composition/module_executor.py` / `mozaiksai/core/runtime/composition/module_context.py` + `tests/test_runtime_persistence_module_injection.py`
  - `mozaiksai/core/runtime/composition/module_event_router.py` + `tests/test_module_loader_contracts.py`
  - generated-app dependency checks + `tests/test_appgenerator_canonical_generation.py` or `tests/test_appgenerator_persistence_alignment.py` when AppGenerator assumptions are touched

Current runtime truth:

- the runtime host is the universal substrate for auth, transport, workflow execution, persistence primitives, and event delivery
- the platform host owns app loading, module execution, module event meaning, runtime extension mounting, admin and profile discovery, and shell or route composition
- `ModuleLoader` validates `module.yaml`, companion contracts, and `runtime_extensions.yaml`
- `ModuleExecutor` builds `ModuleContext`, enforces action permissions and schemas, injects `ctx.persistence`, and emits declared events
- `ModuleEventRouter` is platform-owned interpretation of reactions, notifications, and capability dispatch on top of runtime event transport
- AppGenerator and generated app fixtures may depend on these contracts; inspect the file contracts and fixture tests before changing behavior

Layer boundary rules:

- Runtime = universal substrate for auth, transport, persistence primitives, workflow execution, session state, and event delivery
- Platform = app workspace loading, module registration and execution, module event meaning, app shell, admin and profile wiring, runtime extension mounting, and route or module dispatch
- Factory = build-time generation and file-contract ownership. Do not bury runtime policy in factory prompts, and do not change runtime behavior without checking generator expectations.
- Hosted = external or hosted-only product capabilities. Do not put hosted-only product logic into runtime or platform code.
- Module business logic stays in module handlers, services, and repos. Do not hardcode app-specific workflow or product rules inside runtime internals.
- `mozaiksai.core.auth` authenticates. Do not smuggle authorization or product policy into generic auth adapters.
- `ModuleContext.persistence` is `ctx.persistence`; do not reintroduce `ctx.db`.

Runtime change checklist:

1. Name the affected runtime primitive or primitives: `Application`, `Run`, `ExecutionWorker`, `ExecutionEngine`, `Event`.
2. Identify the smallest owning layer: runtime, platform, factory, hosted, or module code.
3. Check whether the change alters declarative contracts such as `module.yaml`, `contracts/reactions.yaml`, `contracts/notifications.yaml`, `runtime_extensions.yaml`, `app.json`, `shell.json`, or `config/ai.json`.
4. Check whether `factory_app/build_context/AppGenerator/file_contracts.yaml` or generated-app fixtures assume the current behavior.
5. Update loader or host code, docs, and tests together when a contract changes.
6. Preserve multi-tenant safety, engine-agnostic behavior, observability, runtime events, and token accounting.
7. Prefer replacement over retention shims unless the task explicitly asks to preserve an existing app surface.

Common runtime areas:

- Auth and runtime identity:
  - Inspect `mozaiksai/hosts/runtime.py`, `mozaiksai/core/auth/**`, and `tests/test_auth_oidc_discovery.py`.
  - Keep provider-neutral configuration and clear failure modes.
- Module loading and declarative contracts:
  - Inspect `mozaiksai/core/runtime/app/module_loader.py` and `tests/test_module_loader_contracts.py`.
  - Contract changes belong in loader validation, docs, generator file contracts, and tests together.
- Module execution and context:
  - Inspect `mozaiksai/core/runtime/composition/module_executor.py`, `mozaiksai/core/runtime/composition/module_context.py`, and `tests/test_runtime_persistence_module_injection.py`.
  - Keep permission and schema enforcement plus `ctx.persistence` injection here, not in handlers.
- Module reactions, notifications, and capability dispatch:
  - Inspect `mozaiksai/core/runtime/composition/module_event_router.py` and `tests/test_module_loader_contracts.py`.
  - The runtime carries events; the platform owns module-level meaning, notifications, and capability dispatch.
- Runtime extensions:
  - Inspect `mozaiksai/core/runtime/composition/extensions.py`, `mozaiksai/core/runtime/app/module_loader.py`, and `tests/test_module_runtime_extensions.py`.
  - Keep entrypoints module-local under `backend.*` and contract-bound.
- Persistence, data contract, indexes, and migrations:
  - Inspect `mozaiksai/core/runtime/persistence/**`, `tests/test_runtime_persistence_module_injection.py`, and the relevant `tests/test_runtime_persistence_*.py` slice.
  - If generated module behavior changes, also inspect `factory_app/build_context/AppGenerator/file_contracts.yaml` and `tests/test_appgenerator_persistence_alignment.py`.
- WebSocket and session transport:
  - Inspect `mozaiksai/hosts/runtime.py`, `mozaiksai/core/transport/websocket.py`, and `tests/test_runtime_websocket_contract.py`.
  - Preserve resume, buffering, heartbeat, and input or resume semantics.
- Platform shell, app loading, and route or module dispatch:
  - Inspect `mozaiksai/hosts/platform.py`, `tests/test_platform_ai_config_resolution.py`, `tests/test_platform_layout.py`, and `tests/test_platform_shell_p0_fixes.py`.
  - Keep app-root resolution, app-config ownership, and shell or route loading aligned with the current platform contract.

Focused testing guidance:

- Run the narrowest area test first.
- If you changed contracts or host wiring, add the nearest generator or docs slice that depends on that behavior.
- Good starting slices:
  - module contracts and reactions: `python -m pytest tests/test_module_loader_contracts.py -q`
  - runtime extensions: `python -m pytest tests/test_module_runtime_extensions.py -q`
  - persistence injection: `python -m pytest tests/test_runtime_persistence_module_injection.py -q`
  - WebSocket or runtime host behavior: `python -m pytest tests/test_runtime_websocket_contract.py -q`
  - auth discovery or auth config: `python -m pytest tests/test_auth_oidc_discovery.py -q`
  - platform app loading and shell behavior: `python -m pytest tests/test_platform_ai_config_resolution.py tests/test_platform_layout.py tests/test_platform_shell_p0_fixes.py -q`
  - AppGenerator or runtime contract coupling: `python -m pytest tests/test_appgenerator_canonical_generation.py tests/test_appgenerator_persistence_alignment.py -q`
- Do not substitute a broad repo-wide run when a narrower slice can falsify the change.

Final report requirements:

- Always include `Tests run`.
- Always include `OSS Change Impact`.
- Include `Module Contract Impact` when module manifests, loader validation, executor behavior, or module event routing changed.
- Include `Control-Plane / Refinement Impact` when runtime session or control-plane resume behavior or workflow routing contracts changed.
- Include `Build Workflow Sequence Impact` when runtime or platform work also changes sequence or entry routing.
- Include `Managed Capability Boundary Check` when the change touches managed-capability boundaries, facade wiring, or adapter contracts.

Return:

1. affected runtime primitive or primitives
2. owning layer and why
3. declarative contracts or host surfaces changed
4. AppGenerator or generated-app dependency impact
5. tests required or run
6. compatibility or rollout risk





