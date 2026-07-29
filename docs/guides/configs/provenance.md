# App Provenance

`app/provenance.yaml` records where an app bundle came from and which Mozaiks
contracts it declares. It is optional for existing apps, emitted by new
scaffolds and generated app bundles, and loaded by Studio/refinement tooling
when present.

```yaml
schema_version: mozaiks.provenance.v1
app_kind: generated
created_with:
  mode: factory
  mozaiks_ref: package:0.1.10
  workflow: AppGenerator
  workflow_sequence: full_build
  build_id: build_123
contracts:
  app: mozaiks.app.v1
  dashboard: mozaiks.dashboard.v1
  refinement_harness: mozaiks.refinement_harness.v1
overlays:
  dashboard: dashboard/dashboard.yaml
artifact_refs:
  generated_artifact_ids: [artifact_123]
  accepted_artifact_version_ids: []
  app_context_version_ids: []
```

## What It Owns

Provenance owns app-bundle lineage:

- creation source: CLI, factory workflow, import, manual authoring, or App Zero
- Mozaiks package/ref used when the bundle was created or refined
- workflow, workflow sequence, build, artifact, and AppContextVersion refs
- contract schema refs for app-owned declarative files
- thin overlay pointers such as `dashboard: dashboard/dashboard.yaml`

It does not own runtime behavior. `requirements.txt` remains the installed
package source, app registry/artifact records remain live hosted state, and
`app/` contracts remain the source of actual behavior.

## Overlay Semantics

`overlays` is a names-to-relative-path map. Paths must stay inside the app
bundle, cannot be URLs, and cannot be absolute local paths.

Overlay files express app-specific differences from OSS defaults. For example,
an app can declare:

```yaml
overlays:
  dashboard: dashboard/dashboard.yaml
  refinement_policy: config/refinement_policy.yaml
```

That means the app consumes the OSS dashboard and refinement defaults, then
applies the listed app-local files where supported by the corresponding loader.

## Runtime And CI

The runtime exposes these stable entrypoints:

- `mozaiksai.core.runtime.app.AppProvenance`
- `mozaiksai.core.runtime.app.build_default_app_provenance`
- `mozaiksai.core.runtime.app.load_app_provenance`
- `mozaiksai.core.runtime.app.write_app_provenance`
- `mozaiksai.core.runtime.app.AppLoader.load`

`AppLoader.load()` accepts missing provenance for backwards compatibility, but
rejects malformed `app/provenance.yaml` when it is present. CI should load the
app through the pinned `mozaiks` package, validate provenance, and verify that
declared overlay paths remain inside the app bundle.
