# App Intelligence User Journey

This is the product-facing lifecycle for source-backed app building in Mozaiks.
It applies to generated apps, imported repositories, and ongoing refinement.

## Canonical Flow

```text
create/import app
  -> index source
  -> build graph
  -> generate/refine
  -> validate
  -> review diff
  -> promote
```

Mozaiks should make this lifecycle visible in Studio. The user should never
wonder whether the system is reading source, waiting for a repo clone, blocked
on context, editing files, or validating a staged change.

## Create Or Import

Greenfield apps start from the build workflow. Imported apps start from a repo
or local workspace.

The import surface collects:

- repo URL
- branch
- private repo auth connector, when supported by the hosted product
- monorepo path
- ignored paths
- source size limits
- re-index intent

Studio creates or updates the app registry record first. Then it starts a
source import and App Intelligence index job. The app overview becomes the
visible transition surface while the job runs.

## Index Source

Indexing is a background job with explicit phases:

| Phase | Meaning |
| --- | --- |
| `repo_clone` | Clone or prepare the requested source |
| `workspace_scan` | Select supported files through the shared scan policy |
| `source_index` | Build the redacted source bundle |
| `symbol_parse` | Extract symbols, imports, routes, components, and source chunks |
| `context_graph` | Build and persist deterministic relationship facts |
| `app_intelligence` | Synthesize the compact app overview for agents and Studio |
| `ready` | Register the current `AppContextVersion` |

The job state is durable enough for Studio refreshes and API consumers. Public
payloads must not expose absolute workspace paths, raw credentials, or secrets.

## What Agents Receive

Agents do not receive full repositories as prompt text. At each checkpoint, the
context selector chooses:

- app summary
- relevant graph nodes and edges
- likely files
- exact source reads through source-context tools
- stale-context warnings
- validation commands and risk hints

The agent prompt receives compact context first. Exact files are retrieved only
when the workflow or refinement checkpoint has selected them.

## What Studio Shows

Studio shows a source-context panel before edit-heavy workflows proceed:

- readiness: missing, queued, indexing, ready, stale, degraded, failed
- current phase and progress
- detected primary framework
- indexed file count
- graph node and edge counts
- detected frameworks
- validation commands Mozaiks can run
- warnings such as skipped files, stale context, missing tests, or parser issues

This panel is not documentation text inside the build workflow. It is the
operational status of the app context that agents can use.

## Generate Or Refine

Once the current context is ready, AppGenerator, AgentGenerator, existing-app
adoption, and refinement checkpoints all use the same App Intelligence Plane.

Greenfield generation indexes the generated app bundle before future edits.
Repository imports index the selected source root before adoption or refinement.
Refinement reuses the current context and reads exact files only after the
scope is selected.

## Validate

Validation is framework-aware and evidence-backed:

- run detected lint, test, typecheck, build, or framework-specific commands
  when available
- choose executable commands from App Intelligence framework detection, not
  from model-suggested shell text
- run install commands only when explicitly requested
- fall back to syntax checks, schema validation, manifest validation, source
  read checks, and artifact integrity checks when commands are unavailable
- persist validation status with the staged artifact and show validated,
  skipped, or failed results to the user

Tree-sitter provides code facts. Framework detection and manifests provide
validation commands. Runtime artifacts and data contracts provide product and
behavior context.

Studio exposes the first source-backed validation action from the App
Intelligence panel. It uses the latest indexed source root, runs inside an
isolated copy, and returns a compact result with command status, fallback
checks, warnings, and bounded output tails. Refinement paths should use the same
runner after staged files are ready. The proposed files are overlaid into the
isolated validation workspace so review is based on the edited source, not only
the pre-edit index.

## Review And Promote

Mozaiks stages edits as reviewable artifacts. The review surface should show:

- changed files
- graph-related impacted files
- validation results
- stale-context warnings
- ownership boundary warnings
- whether the change can be promoted, needs another workflow, or needs user
  approval

Promotion is explicit. Promotion registers the new artifact state and refreshes
or invalidates App Intelligence so later agents do not work from stale context.
Normal accept and promote actions require `passed` validation. `skipped` or
`pending` validation requires an explicit operator override, and `failed`
validation requires revision or rejection.

## FalkorDB And Tree-Sitter Roles

Tree-sitter is the local parser layer. It extracts deterministic code facts such
as symbols, imports, functions, classes, routes, and components.

FalkorDB is the production relationship-query mirror. It should mirror files,
symbols, modules, pages, actions, imports, routes, data contracts, validations,
and artifact/source refs from canonical records. It is not the source of truth.
Artifact versions, source index records, and `AppContextVersion` remain
authoritative.

## Private Repo Boundary

OSS source import supports local workspaces and public HTTP(S) Git repositories.
Private repo import requires a connector-backed credential resolver owned by
the hosted product or operator deployment. Repository URLs must never contain
embedded credentials.
