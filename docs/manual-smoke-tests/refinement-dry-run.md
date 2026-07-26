# Refinement Dry Run

The refinement dry-run harness previews how the Refinement Engine would stage a
refinement request without mutating app files or running generation workflows.
It performs classification, route resolution, impact derivation, and LLM profile
reference resolution, then emits a JSON-safe plan.

The dry run always reports:

```text
No files were changed.
```

## Offline Run

The default mode is offline. It does not call the LLM. You can provide an
explicit change class:

```powershell
python scripts/dry_run_refinement.py `
  --request "Add an archive_project action to the projects module API." `
  --change-class feature
```

If `--change-class` is omitted, the harness uses a deterministic classifier for
the neutral dry-run fixture requests.

Machine-readable output:

```powershell
python scripts/dry_run_refinement.py `
  --request "Change the dashboard experience to highlight reports first." `
  --change-class design `
  --json
```

Save a plan to an explicit path:

```powershell
python scripts/dry_run_refinement.py `
  --request "Change the analytics_provider connector sync behavior." `
  --change-class patch `
  --save-plan .tmp-dry-run/refinement-plan.json
```

The script refuses to save plans under `generated/` or `generated_apps/`.

## Live Classifier

Use `--live-classifier` only when you intentionally want to validate the
configured classifier profile:

```powershell
python scripts/dry_run_refinement.py `
  --request "Change managed analytics dashboard display." `
  --live-classifier `
  --json
```

This calls the configured `classifier` LLM profile, then performs the same
deterministic route and impact dry run. It still does not execute AppGenerator,
DesignDocs, or any workflow sequence.

## Manifest Input

When no manifest is provided, the harness uses a neutral built-in app bundle
manifest with app config, data contract, integration adapters, modules, and UI
pages.

To use a custom manifest:

```powershell
python scripts/dry_run_refinement.py `
  --request "Add a required project phase field and migrate existing project records." `
  --change-class feature `
  --manifest path/to/files-manifest.json `
  --json
```

The manifest may be either a JSON list of paths/objects or an object with a
`files_manifest` list.

## Output Shape

The dry-run plan includes:

- request and artifact kind
- change class and inferred refinement lane
- target workflow and workflow sequence
- affected declarative families
- affected bundle paths
- scope summary
- profile references for classifier, planner/codegen, and reviewer validation
- `execution_mode: dry_run`
- `mutation_allowed: false`
- `generated_files_changed: false`
- next-step recommendation
- warnings, including `No files were changed.`

## How It Differs From Execution

Dry run stops after deterministic planning. It does not:

- run AppGenerator or any workflow sequence
- write generated app files
- patch source files
- promote artifacts
- update model settings

Use it before building a real staged execution harness or before introducing a
human approval gate for mutations.

