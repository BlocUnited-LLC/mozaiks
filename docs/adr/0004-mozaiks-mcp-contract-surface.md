# ADR 0004: Mozaiks MCP Contract Surface And Read/Write Boundary

Date: 2026-08-19

Status: proposed

## Decision

Expose Mozaiks app contracts and validation to external coding agents through an
MCP server that is **read and validate only**. The v1 tool surface is a thin
adapter over current `mozaiksai/` contract models, loaders, and validation
helpers. It may normalize those results into MCP response envelopes, but it must
not introduce a second source of truth for app identity, layout, modules, pages,
or validation semantics. Any tool that mutates an app bundle, stages artifacts,
promotes versions, executes commands in a user workspace, or decides edit scope
is out of scope for v1 and requires a separate ADR.

MCP is an adapter at the coding-agent boundary. It does not replace the
refinement loop, and it does not become a second refinement path.

## Reason

A developer who clones a Mozaiks-generated app and opens it in Claude Code,
Codex, or Cursor is outside the Refinement Harness. Today that agent has to infer
the app's canonical structure from copied framework files, which are
framework-internals guidance rather than app-development guidance, and which
drift from the validators they describe.

MCP lets the agent ask the framework directly instead of inferring. Managed
refinement and local development should consume the same framework contracts,
but not the same authority boundary:

- Managed refinement: Harness decides scope and staging authority, coding
  worker implements, framework validates.
- Local development: coding agent connects over MCP for framework reads and
  local validation only; no harness authority is delegated to the agent.

The current dependency is `ag2[a2a,openai,tracing]==1.0.1`, and that package
contains MCP support modules such as `ag2.mcp` and MCP server/toolkit helpers.
This ADR chooses that protocol boundary rather than inventing a Mozaiks-specific
agent protocol. The implementation ADR/PR must still verify the exact AG2 MCP
API used by Mozaiks at implementation time.

The boundary needs deciding now because a published MCP tool surface is a public
protocol that third-party clients depend on, which `OSS_PUBLICATION_POLICY.md`
classifies as a one-way door.

## Alternatives Considered

- **Static instruction pack only.** Ship a generated-app skill pack through
  `mozaiks dev init/update`, no MCP. Simpler, needs no running process, and works
  in clients without MCP support. Not chosen as the sole approach because
  markdown describing a contract drifts from the code that enforces it, and
  guidance can only advise where a tool can fail the agent. Retained as the
  fallback layer, not the only layer.

- **Copy framework `CLAUDE.md` and `AGENTS.md` into generated apps.** This is
  current behaviour and is rejected. The content is about changing Mozaiks
  itself, not about iterating on an app Mozaiks produced, and the copies become a
  second non-authoritative source of truth.

- **A Mozaiks-specific agent protocol.** Rejected. AG2 owns agent protocol
  concerns under the AG2 ownership boundary in `CLAUDE.md`, and MCP is already
  supported there.

- **Full read/write MCP in v1.** Rejected for now. Mutation through MCP is a new
  authority surface that could become a promotion path bypassing harness
  classification, scoping, and staging. Deferred to its own ADR rather than
  decided implicitly by implementation.

## Consequences

### What becomes easier

- A local coding agent can resolve canonical structure, existing modules, and
  contract violations from the framework instead of guessing.
- Generated `AGENTS.md` and `CLAUDE.md` can shrink to identity, invariants,
  static fallback guidance, and a pointer at MCP, which removes the copied
  framework-internals drift problem. The current CLI generation and
  synchronization path lives in `mozaiks_cli.agent_guidance` and
  `mozaiks_cli.commands.sync_agent_guidance`.
- The harness and local development consume one set of capabilities, so app
  contracts stay the single source of truth.

### What becomes harder

- The tool surface becomes a public contract with the compatibility obligations
  that implies. Renaming or removing a tool is a breaking change for third-party
  clients.
- Two guidance layers exist (MCP and static fallback) and must not disagree.
- Local validation coverage is narrower than harness validation, and that gap has
  to be communicated rather than hidden.

### Contract and boundary changes

- Adds a new public protocol surface: the MCP tool list plus exact request,
  response, error, and version-envelope shapes.
- Adds no new authority. Every v1 tool is a read or a pure validation over a
  path the caller already has on disk.
- Requires fail-closed workspace path resolution before any tool reads the
  filesystem.

## Proposed v1 Tool Surface

Every tool below wraps an existing `mozaiksai/` backing surface. None introduce
new capability. The implementation may add an MCP adapter function per tool, but
those adapters must call the backing surfaces listed here and return only
schema-versioned, JSON-serializable data.

| Tool | Backing function | Nature |
|---|---|---|
| `get_workspace_identity` | `core/runtime/app/provenance.py` (`load_app_provenance`, `resolve_app_provenance_path`, `AppProvenance`) | read |
| `get_canonical_layout` | `core/runtime/app/layout_registry.py` (`default_app_layout_registry`, `AppLayoutRegistry`) | read |
| `resolve_artifact_location` | `core/runtime/app/layout_registry.py` (`match_path`, `validate_registered_path`) | read |
| `list_modules` | `core/runtime/app/module_loader.py` (`ModuleLoader.discover_module_names`) | read with constructor side effects constrained by path containment |
| `list_pages` | `core/runtime/app/page_schema.py` (`discover_page_schema_paths`) | read |
| `validate_app_bundle` | `control_plane/app_validation.py` (`run_app_validation_fallback_checks`) | pure validation; not equivalent to AppGenerator acceptance |
| `plan_validation_commands` | `control_plane/app_validation.py` (`plan_app_source_validation_commands`) | read, plans without executing |

`validate_app_bundle` is intentionally narrow in v1. It means local deterministic
fallback validation over a resolved workspace root: JSON manifest parse, Python
syntax compile, and YAML manifest parse. It does **not** mean
`factory_app/workflows/AppGenerator/tools/app_validation.py`
`run_app_bundle_acceptance_gate`, and it must not be described as promotion,
export, or generated-app acceptance authority.

### Protocol envelope

The MCP contract version must be explicit in every tool response, not only
declared in `app/provenance.yaml`. Every successful v1 response uses this
minimum envelope:

```json
{
  "protocol_schema_version": "mozaiks.mcp.v1",
  "tool": "tool_name",
  "workspace": {
    "root": "absolute-resolved-path",
    "app_root": "absolute-resolved-path/app",
    "app_id": "optional-app-id",
    "provenance_schema_version": "optional-provenance-version"
  },
  "result": {},
  "diagnostics": []
}
```

Every failure uses the same `protocol_schema_version` plus a stable `error`:

```json
{
  "protocol_schema_version": "mozaiks.mcp.v1",
  "tool": "tool_name",
  "error": {
    "code": "workspace_not_found",
    "message": "Workspace root could not be resolved inside the allowed root."
  },
  "diagnostics": []
}
```

The implementation PR must define exact Pydantic models or equivalent strict
schemas for each `result` payload before the tool surface ships. A prose table is
not sufficient for a public protocol.

### Path and workspace containment

Every tool request must include either no path, meaning the MCP process
workspace root, or one workspace-relative path. The server resolves paths with
`Path(...).expanduser().resolve()` and fails closed when:

- the workspace root does not exist or is not a directory;
- the resolved app root does not contain `app/app.json` or an equivalent
  canonical app root selected by the implementation contract;
- any requested path is absolute when the tool expects a relative path;
- any requested path escapes the resolved workspace root;
- any requested path contains `..`, a drive prefix, a URL/scheme, glob
  characters, or a symlink escape after resolution.

`list_modules` must account for the current `ModuleLoader` constructor adding
import roots to `sys.path`; the MCP adapter may use it only after containment is
proven, and must not call `ModuleLoader.load()` because loading executes module
handler code.

### Explicitly excluded from v1

- **`run_app_source_validation` with `confirm_execution=True`.** This executes
  subprocesses in a workspace. The function already treats execution as
  privileged, with opt-in confirmation, argv parsing, executable allowlisting,
  and working-directory containment. Exposing it over MCP would let a remote
  caller trigger command execution, which is an authority decision, not a
  packaging decision. Planning is exposed; execution is not.

- **`run_refinement_validations`.** It is pure, but it requires a
  `RefinementExecutionPlan` and `RefinementStagingResult` and rejects any plan
  whose `execution_mode` is not `staged`. Those are harness constructs. A local
  agent has no plan and no staging area, so this validator is structurally
  harness-only. `run_app_validation_fallback_checks` takes only a workspace root
  and is the correct local equivalent.

- **"Which files may this change touch."** No backing function exists, and it is
  the most authority-adjacent tool proposed. It expresses allowed scope, which is
  a harness responsibility. Deferred.

- **Any create, edit, stage, or promote tool.**

- **Factory-generated application MCP bindings.** Issue #338 tracks a separate
  architecture concern: how Factory-generated applications consume or expose MCP
  bindings through build-context/capability-pack contracts. That issue is not
  implemented or approved by this ADR.

## Which Validator MCP Calls

MCP calls `mozaiksai/control_plane/`, never `factory_app/`.

Three `app_validation.py` files exist:

- `mozaiksai/control_plane/app_validation.py` (framework)
- `factory_app/refinement_harness/tools/app_validation.py` (factory pack)
- `factory_app/workflows/AppGenerator/tools/app_validation.py` (workflow-local)

Calling a `factory_app/` validator would make local development depend on
first-party factory policy. That breaks the separation `SessionRouter` already
maintains by accepting an injected `TriggerRouteResolver` rather than importing
the harness, and it would contradict invariant 7.

This also preserves the managed Refinement Harness boundary. The Harness may use
MCP-accessible framework reads and validations as evidence, but Harness authority
still lives in its checkpoint routing, staging, plan, and promotion contracts.
Local MCP callers do not receive those authority objects and must not infer
allowed edit scope from read-only framework metadata.

## Reversibility

Medium risk.

The implementation is thin and removable, but the tool names and response shapes
become a public contract once third-party clients bind to them. Renaming a tool
after adoption requires a deprecation path.

Choosing read-only for v1 is the reversible direction: adding mutation later is
additive, whereas shipping mutation and withdrawing it is a breaking change and a
security regression in clients that came to rely on it.

## Affected Invariants

- **#3 Agents produce candidates; deterministic code validates and promotes.**
  Upheld. MCP exposes validation, never promotion. Promotion stays explicit and
  harness-owned.
- **#6 Authority bypass semantics must not expand casually.** This is the
  invariant the read/write boundary exists to protect. v1 adds no authority.
  Command execution and scope decisions are excluded for exactly this reason.
- **#7 Mozaiks App dogfoods public framework contracts.** Upheld by requiring MCP
  to call framework validators rather than factory pack ones.
- **#4 Public schemas and contracts are classified and versioned.** The MCP tool
  surface must carry an explicit response envelope version,
  `mozaiks.mcp.v1`. App provenance should additionally declare the supported MCP
  contract under `contracts:`, but provenance is metadata, not the wire envelope.

## Historical Notes

PR #302 is not an MCP-related precursor. It was the generated quickstart
workspace Git-ignore fix, merged on 2026-08-19. Do not cite PR #302 as MCP
history. The related MCP follow-up concern is issue #338, which covers
deterministic MCP capability bindings inside Factory-generated applications and
is out of scope for this developer-facing read/validate MCP ADR.

## OSS Boundary

Open interface, reviewed implementation.

The tool surface, the request and response shapes, and the read-only
implementation are framework capabilities and belong in OSS. Any hosted-only
behaviour layered on top, such as operator credentials, cross-app knowledge, or
deployment actions, stays outside this contract and is a separate review.

The v1 server must not expose hosted operations, deployment execution, payment
operations, credential values, production authority, managed-provider mutation,
marketplace ranking, cross-app intelligence, customer-derived repair data, or
operator KnowledgeStore contents. It may expose only one-workspace framework
metadata and deterministic validation derived from files already present in the
caller-selected workspace.

## Validation

Before merge of the implementation that follows this ADR:

- Contract test asserting the v1 tool list, so adding a tool is a deliberate
  contract change rather than an accident.
- Test asserting no v1 tool writes to the workspace, for example by running the
  full surface against a read-only fixture and asserting no filesystem mutation.
- Test asserting `confirm_execution` cannot be set through any MCP tool.
- Test asserting MCP imports only from `mozaiksai/`, never from `factory_app/`,
  which is mechanically checkable and could become a governance guardrail.
- Test asserting the declared MCP contract version is present in
  `app/provenance.yaml` under `contracts:`.
- Test asserting every response contains `protocol_schema_version:
  mozaiks.mcp.v1`.
- Test asserting workspace path resolution fails closed for missing roots,
  absolute paths where relative paths are required, traversal, symlink escapes,
  URL/scheme-shaped paths, and paths outside the allowed workspace root.
- Test asserting `validate_app_bundle` does not execute subprocesses, does not
  call `ModuleLoader.load()`, and is not wired to the workflow-local
  AppGenerator acceptance gate.
