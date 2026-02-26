# Agent Bootstrap Prompt
> Last updated: 2026-02-23

---

You are starting fresh with zero prior context. Work only from repository state and the docs listed below.

## Repository context
- Repo: `mozaiks` (unified stack repo).
- Formerly three repos: kernel (contracts), core (runtime), ai (orchestration) — now `src/mozaiks/{contracts,core,orchestration}`.
- See `AGENTS.md` for full package tree, layer rules, and import direction.
- Local paired consumer repo (if present in your workspace):  
  `<path-to-consumer-platform-repo>`

## Frontend migration context
- Shared frontend logic was migrated into this repo and must be treated as real source, not temporary noise:
  - `packages/frontend/shell/src` (shared shell orchestration, especially `pages/ChatPage.js`)
  - `packages/frontend/chat-ui/src` (shared UI state machine/primitives, especially `state/uiSurfaceReducer.js`)

## Read these docs first (authoritative)
- `AGENTS.md` (root — agent operating guide, layer rules, package map)
- `docs/architecture/source-of-truth/README.md`
- `docs/architecture/source-of-truth/UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/GRAPH_INJECTION_CONTRACT.md`
- `docs/architecture/source-of-truth/LEARNING_LOOP_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/APP_CREATION_GUIDE.md`

## Runtime Alignment (Repo Context)
Use repo-owned runtime boundaries (see WORKFLOW_ARCHITECTURE.md + PROCESS_AND_EVENT_MAP.md):

- **Shared runtime (`core`)** — API app, auth, persistence, streaming, event routing/dispatch, workflow config loading. Maps to `src/mozaiks/core/`.
- **Execution runtime (`orchestration`)** — workflow run execution, scheduling/state machine, runner adapters, checkpoint/resume behavior. Maps to `src/mozaiks/orchestration/`.
- **Workflow files** (`orchestrator.yaml`, `agents.yaml`, `tools.yaml`, stubs/components) are **inputs consumed by runtime**, not a runtime layer inside this repo.

Keep process and event ownership explicit when classifying modules.

## Three execution modes (architectural constraint)
- Mode 1: AI Workflow (chat → agent → artifact, WebSocket, full orchestration runtime)
- Mode 2: Triggered Action (button → mini-run or function, may or may not use AI)
- Mode 3: Plain App (pages, CRUD, settings — no AI, reads persisted artifacts)
- Artifacts bridge the modes. Do not conflate them.

## Critical UI contract to preserve
- `conversationMode`: `ask | workflow`
- `layoutMode`: `full | split | minimized | view`
- `surfaceMode`: `ASK | WORKFLOW | VIEW`
- `view` is UI fullscreen surface mode, not compute sandbox.
- Sandbox runtime (for example E2B preview) is separate infrastructure rendered inside artifact/view surfaces.

## Design philosophy
- Consumer platforms/apps built on mozaiks infrastructure are not unique to any single repo.
- OSS members building their own platforms should have the same seamless experience.
- Therefore: push reusable runtime logic into `mozaiks`, keep platform thin (declarative YAML/JSON + minimal stubs). Patterns should generalize to any consuming platform/app.
- In docs and code comments, use neutral language: "how platforms should structure AI + non-AI UI when using `mozaiks` infrastructure." Do not frame ownership as specific to a single platform repo.

## Primary objective
- Execute a clean API + architecture alignment pass:
  1. **API convergence:** Define and implement clean `mozaiks` public APIs (no backward-compatibility requirement in this pre-production phase), while aligning active consumer repos in lockstep.
  2. **Structural alignment:** Verify that `src/mozaiks/core/` maps to shared-runtime responsibilities and `src/mozaiks/orchestration/` maps to execution-runtime responsibilities. Flag any code that lives in the wrong runtime boundary.
  3. **Event model alignment:** Verify that runtime event types match EVENT_TAXONOMY.md and the event flow matches PROCESS_AND_EVENT_MAP.md.

## Consumer API convergence targets (for active consumer repos)

These are current platform expectations and should be treated as baseline inputs for API convergence (not immutable backward-compat requirements):

1) `mozaiks.core.auth`
Expected symbols:
- `AuthConfig`, `get_auth_config`
- `authenticate_websocket`, `authenticate_websocket_with_path_user`, `authenticate_websocket_with_path_binding`
- `verify_user_owns_resource`, `require_resource_ownership`, `require_user_scope`
- `WebSocketUser`, `UserPrincipal`, `ServicePrincipal`
- `require_user`, `require_any_auth`, `require_internal`, `require_role`, `optional_user`
- `WS_CLOSE_POLICY_VIOLATION`

2) `mozaiks.core.artifacts`
Expected symbols:
- `inject_bundle_attachments_into_payload`
- `iter_bundle_attachment_files`
- `handle_chat_upload`

3) `mozaiks.core.persistence`
Expected symbols:
- `PersistenceManager`
- `AG2PersistenceManager`

4) `mozaiks.core.streaming`
Expected symbol:
- `SimpleTransport`

5) `mozaiks.core.tools.ui`
- Verify and normalize exports: `emit_tool_progress_event`, `use_ui_tool`.

6) `mozaiks.orchestration`
Expected symbol:
- `KernelAIWorkflowRunner` (must remain working)

## Rules
- Prioritize clean, coherent APIs over preserving legacy symbol names.
- Reuse and extend existing framework modules where possible; do not rebuild equivalent systems from scratch.
- Before creating any new module, search the repo for existing modules with similar names or purpose. Do not duplicate.
- Do not add any import from `mozaiks.orchestration` inside `mozaiks.core` (layer violation).
- If an old symbol/path should be changed or removed, provide exact paired consumer-repo update steps.
- Apply paired refactor patches side-by-side in both local repos:
  - `C:\Users\mbari\OneDrive\Desktop\BlocUnited\BlocUnited Code\mozaiks`
  - `<path-to-consumer-platform-repo>`
- Treat cross-repo alignment as a hard gate:
  - Every `mozaiks` API/runtime change must include either a paired consumer-repo patch or explicit evidence of "no platform impact".
  - "No platform impact" requires symbol/path search evidence from the consumer repo with file references.
- Keep docs aligned with actual file paths and behavior in this repo, especially frontend surface logic.
- Enforce platform minimalism during convergence:
  - Prefer `.yaml` / `.json` declaratives in consumer repos.
  - Keep `.py` / `.js` / `.tsx` / `.cs` stubs thin and minimal.
  - Move generic runtime logic into `mozaiks` instead of duplicating it in platform.

## Do not modify
- `docs/architecture/source-of-truth/*.md` — unless the change fixes a file path or symbol name that no longer matches actual code. Never rewrite architectural prose.
- `AGENTS.md` — unless updating to reflect a structural change you made.

## Execution checklist
1. Verify both local repos are accessible and writable:
   - `C:\Users\mbari\OneDrive\Desktop\BlocUnited\BlocUnited Code\mozaiks`
   - `<path-to-consumer-platform-repo>`
2. Read WORKFLOW_ARCHITECTURE.md (especially runtime structure, AG-UI boundary, and three execution modes) and LEARNING_LOOP_ARCHITECTURE.md.
3. Audit current platform expectations vs current exports and propose a target clean API contract.
4. Audit module placement: does every module in `core/` belong to shared runtime responsibilities? Does every module in `orchestration/` belong to execution runtime responsibilities? Flag misplacements.
5. Implement the target clean exports/modules/adapters as needed (not required to preserve legacy names).
6. Add import smoke tests for the target public API contract. File: `tests/test_public_api_contract.py` (or equivalent).
7. Add/adjust unit tests for new/refactored modules and any adapter layers kept intentionally.
8. Run tests/lint/type checks.
9. Apply paired consumer-repo refactor patches side-by-side in the local workspace, or provide search-backed evidence for "no platform impact".
10. Update public API docs and architecture docs where needed (without breaking source-of-truth consistency).

## Required output format
1. API convergence matrix:
   - current platform expectation (import path/symbols)
   - current stack status (`exists | partial | missing`)
   - target clean API
   - action taken in `mozaiks`
   - required paired action in the consumer repo
   - file paths changed
2. Code change summary.
3. Test evidence and command outputs.
4. Documentation updates made.
5. Layer alignment findings:
   - module path
   - current layer (`core` or `orchestration`)
   - expected layer per architecture docs
   - action needed (`correct | misplaced | needs refactor`)
6. Residual risks/gaps and exact next steps.
7. Platform minimalism report:
   - declarative artifacts introduced/updated (`.yaml` / `.json`)
   - stubs touched (`.py` / `.js` / `.tsx` / `.cs`)
   - rationale for any non-minimal imperative additions

## Acceptance criteria
- Target clean API contract is implemented and tested in `mozaiks`.
- Required paired platform updates are applied side-by-side in the local consumer repo.
- Tests pass.
- UI source-of-truth remains consistent with actual code.
- `view != sandbox` boundary is explicit in docs and preserved in implementation.
- No shared-runtime code lives in `orchestration/` and no execution-runtime code lives in `core/`.
- The agent can justify module placement using process/event ownership and runtime responsibilities.
- Consumer repo remains declarative-first with minimal imperative stubs.

---

## Execution Request (after bootstrap understanding)

Use this as the immediate follow-up message after sending the bootstrap prompt above:

```text
Execute now. Do not just plan.

0) Verify both local repos are accessible before coding:
   - `C:\Users\mbari\OneDrive\Desktop\BlocUnited\BlocUnited Code\mozaiks`
   - `<path-to-consumer-platform-repo>`

1) Audit and build an API convergence matrix from current platform expectations:
   - `mozaiks.core.auth`
   - `mozaiks.core.artifacts`
   - `mozaiks.core.persistence`
   - `mozaiks.core.streaming`
   - `mozaiks.core.tools.ui`
   - `mozaiks.orchestration`

2) Implement target clean exports/adapters in `mozaiks`.
   - No backward-compatibility requirement (pre-production refactor).
   - Reuse existing framework modules where possible; avoid duplicating systems.
   - For every changed/removed symbol, apply paired consumer-repo patch in the local workspace.
   - If you claim "no platform impact", include grep/search evidence and file references from the consumer repo.

3) Validate architecture alignment:
   - Check `src/mozaiks/core/` maps to shared-runtime responsibilities.
   - Check `src/mozaiks/orchestration/` maps to execution-runtime responsibilities.
   - Flag and classify any misplaced modules.

4) Validate event alignment:
   - Runtime event names/types must align with `EVENT_TAXONOMY.md`.
   - Runtime event flow must align with `PROCESS_AND_EVENT_MAP.md` and `EVENT_SYSTEM_ARCHITECTURE.md`.

5) Add tests:
   - Create/extend `tests/test_public_api_contract.py` with import smoke assertions for the target API contract.
   - Add unit tests for any adapter/facade added.

6) Run verification:
   - `pytest tests/ -v`
   - `mypy src/mozaiks/`
   - `ruff check src/`

7) Keep UI contract intact:
   - preserve `conversationMode`, `layoutMode`, `surfaceMode` semantics.
   - preserve the boundary that `view` is a UI mode, not sandbox runtime.

Required final output:
- API convergence matrix (current expectation, target API, stack action, platform action, changed files)
- Code changes summary
- Test/lint/type-check evidence
- Doc updates made
- Layer alignment findings (`correct | misplaced | needs refactor`)
- Platform minimalism report (declaratives vs stubs, and why)
- Residual gaps and exact next steps
```
