# Framework Detection

Framework detection is the mechanism by which the App Intelligence Plane
identifies the languages, package managers, frameworks, entrypoints, and
validation commands present in a source workspace before agents read or modify
the app.

It is a pure function: given a file map (relative path → content), it returns
a typed `FrameworkDetectionResult`. It never reads the filesystem directly.
The indexer calls it after scanning source files into memory.

## Why It Exists

Agents must know what kind of app they are working with before they can
produce useful lint, typecheck, test, or build commands. Framework detection
makes that knowledge explicit and deterministic, so agents receive structured
facts rather than rediscovering the same signals on every request.

## Output Contract

```python
class FrameworkDetectionResult(BaseModel):
    schema_version: "mozaiks.framework_detection.v1"
    primary_framework_id: str | None     # highest-confidence ranked framework
    primary_framework_label: str | None  # human-readable label
    languages: list[str]                 # python, typescript, javascript, php, css
    package_managers: list[str]          # npm, pnpm, yarn, python, composer
    manifests: list[str]                 # discovered manifest paths
    frameworks: list[DetectedFramework]  # sorted by confidence descending
    entrypoints: list[FrameworkEntrypoint]
    validation_commands: list[FrameworkValidationCommand]
    monorepo: bool
    warnings: list[str]
```

The result is stored inside `AppIntelligenceSnapshot.architecture` under the
`framework_detection` key. It is also written onto each
`AppIntelligenceIndexJob` record so the Studio panel and validation runner
can access it without re-running the indexer.

## Evidence Model

Detection is evidence-based. Each detected framework accumulates a list of
`FrameworkEvidence` items:

| Evidence kind | What it means | Weight |
|---|---|---|
| `manifest` | A recognized manifest file found at a relevant path | 0.22 |
| `dependency` | A framework-specific package declared in a manifest | 0.25 |
| `script` | A recognized npm/composer script name found in a manifest | 0.12 |
| `source` | A framework-specific import or construct found in source code | 0.18 |
| `path` | A well-known directory or file pattern at a recognized location | 0.14 |

Confidence = min(0.98, 0.35 + sum of evidence weights). A single strong
signal (e.g., a `package.json` declaring `next` as a dependency plus a `next`
script) is enough to reach ~0.82 confidence.

## Supported Frameworks

| Framework ID | Label | Category | Primary signals |
|---|---|---|---|
| `mozaiks_app` | Mozaiks App | `mozaiks` | `app/app.json`, `app/modules/*/module.yaml` |
| `mozaiks_factory` | Mozaiks Factory | `mozaiks` | `factory_app/workflows/`, `workflows/AppGenerator/` |
| `node` | Node.js | `app_runtime` | any `package.json` |
| `nextjs` | Next.js | `fullstack` | `next` in dependencies |
| `react` | React | `frontend` | `react` in dependencies |
| `vite` | Vite | `build_tool` | `vite` in dependencies or `vite.config.*` |
| `vue` | Vue | `frontend` | `vue` in dependencies |
| `express` | Express | `backend` | `express` in dependencies |
| `python` | Python | `app_runtime` | `pyproject.toml` or `requirements.txt` |
| `fastapi` | FastAPI | `backend` | `fastapi` dependency or import in source |
| `django` | Django | `backend` | `django` dependency + urlpatterns/settings source |
| `flask` | Flask | `backend` | `flask` dependency or import in source |
| `laravel` | Laravel | `fullstack` | `laravel/framework` in `composer.json` or `artisan` file |

## Primary Framework Selection

When multiple frameworks are detected, `primary_framework_id` is resolved by
a ranked preference order, with confidence as tiebreaker:

```
mozaiks_app → nextjs → laravel → fastapi → django
→ react → vue → express → flask → node → python
```

The first entry in that preference order with a detected framework wins.
Unknown frameworks fall through with confidence as the only ranking signal.

## Validation Command Emission

Each detector emits `FrameworkValidationCommand` entries for the commands it
believes will work on the detected setup:

- **Node/npm/pnpm/yarn** — emits the commands declared in `package.json`
  scripts (`lint`, `test`, `build`, `typecheck`, `dev`, `start`) plus an
  `install` command for the detected package manager.
- **Python** — emits `python -m pytest` (test, confidence 0.65) and
  `ruff check .` (lint, confidence 0.55) for any `pyproject.toml` or
  `requirements.txt` found.
- **Laravel/PHP** — emits `php artisan test` (test, confidence 0.75).

These candidates are passed to the source validation runner. The runner
filters them by allowed executables and kind, then executes them in order.

## Entrypoints

The detector emits `FrameworkEntrypoint` records for well-known structural
files:

| Entrypoint kind | Meaning |
|---|---|
| `app_manifest` | `app/app.json` — Mozaiks app root |
| `module_manifest` | `app/modules/{id}/module.yaml` — Mozaiks module |
| `route` | Next.js page or app router file |
| `frontend_entrypoint` | React/Vue `App.*`, `main.*`, `index.*` under `src/` |
| `server_entrypoint` | Express `server.js`, `index.js` that contains `express` |
| `api_app` | FastAPI or Flask application source |
| `django_config` | Django `settings.py` or route file |
| `route_file` | Laravel route file under `routes/*.php` |

Entrypoints are stored in the `AppContextGraph` and surfaced in Studio so
agents can navigate directly to structural files when scoping changes.

## Monorepo Detection

`monorepo: true` is emitted when:
- More than one directory contains a `package.json` or `pyproject.toml`
  (excluding the root), or
- A recognized monorepo workspace marker is present: `pnpm-workspace.yaml`,
  `lerna.json`, `turbo.json`, or `nx.json`.

When `monorepo: true`, the source import `monorepo_path` field lets you
scope the import to a specific package within the workspace.

## Recognized Manifest Filenames

The following filenames are tagged as manifests in the detection output:
`package.json`, `pyproject.toml`, `requirements.txt`, `composer.json`,
`vite.config.ts`, `vite.config.js`, `next.config.js`, `next.config.mjs`,
`app.json`, `module.yaml`, `module.yml`.

## Warnings

The result carries `warnings` when detection cannot make confident judgments:

| Warning | Meaning |
|---|---|
| `framework_detection_no_frameworks_detected` | No known frameworks found in the file map |
| `framework_detection_no_validation_commands_detected` | No runnable commands could be emitted |

## Implementation Map

| Concern | File |
|---|---|
| Detection entry point | `mozaiksai/core/app_context/framework_detection.py` — `detect_frameworks_from_file_map()` |
| Result schema | `mozaiksai/core/app_context/framework_detection.py` — `FrameworkDetectionResult` |
| Evidence model | `mozaiksai/core/app_context/framework_detection.py` — `FrameworkEvidence`, `DetectedFramework` |
| Validation command model | `mozaiksai/core/app_context/framework_detection.py` — `FrameworkValidationCommand` |
| Called by indexer | `mozaiksai/core/app_context/indexer.py` |
| Stored on snapshot | `mozaiksai/core/app_context/intelligence.py` — `architecture.framework_detection` |
| Stored on index job | `mozaiksai/control_plane/app_intelligence_jobs.py` — `app_intelligence.framework_detection` |
| Consumed by validation runner | `mozaiksai/control_plane/app_validation.py` — `_command_candidates()` |
| Tests | `tests/test_framework_detection.py` |

## What Framework Detection Is Not

- It is not a build runner. It emits command candidates; the validation
  runner executes them.
- It is not a security boundary. Executables are allowlisted in the
  validation runner, not here.
- It is not an agent prompt. The result is stored as structured data. Agents
  receive a compact summary from `AppIntelligenceSnapshot`, not raw detection
  output.
- It is not the authority on what commands are valid. That belongs to the
  validation runner's executable allowlist.
