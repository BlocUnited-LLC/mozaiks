# Source Import Contracts

Source import is how the App Intelligence Plane acquires a local workspace
root before indexing begins. It is a mandatory step before any source scan,
tree-sitter extraction, framework detection, or graph build can happen.

Related documents:

- [App Intelligence Plane](../foundations/app-intelligence-plane.md)
- [Framework Detection](../foundations/framework-detection.md)
- [Source Validation Framework](source-validation-framework.md)

---

## Two Import Kinds

```python
SourceImportKind = Literal["local_workspace", "git_repository"]
```

### `local_workspace`

The source root is a directory already present on the machine running Studio.
The resolver validates that the path exists and is a directory, then selects
the workspace root (applying `monorepo_path` if provided).

Use this when Studio is running on the same machine as the app workspace.

### `git_repository`

The resolver clones a remote repository to `generated/source_imports/` before
indexing. This supports the "import a public Git repo" flow in Studio.

The clone is:
- shallow (`--depth 1`) for speed
- scoped to a specific branch when `branch` is provided
- placed at a deterministic path derived from `app_id` and the repo slug

After cloning, the `commit_sha` is captured and stored on the
`SourceImportResult` for provenance.

**OSS restriction:** Only public HTTP(S) repositories are supported. URLs
must not contain embedded credentials (username/password in netloc).
Authenticated repository imports require a connector-backed credential
resolver available only in hosted product deployments.

---

## Request Schema

```python
class SourceImportRequest(BaseModel):
    source_kind: SourceImportKind = "local_workspace"
    workspace_root: str | None      # required for local_workspace
    repo_url: str | None            # required for git_repository; http(s) only
    branch: str | None              # optional; max 240 chars
    monorepo_path: str | None       # optional subpath within the workspace
    auth_connector_id: str | None   # always raises ValueError in OSS
    ignored_paths: list[str]        # merged into scan policy exclusions
```

---

## Result Schema

```python
class SourceImportResult(BaseModel):
    source_kind: SourceImportKind
    workspace_root: str     # absolute path to the clone/local root
    selected_root: str      # workspace_root / monorepo_path (or workspace_root)
    repo_url: str | None
    branch: str | None
    commit_sha: str | None  # git_repository only
    monorepo_path: str | None
    ignored_paths: list[str]
    warnings: list[str]
    metadata: dict[str, Any]
```

`selected_root` is what the indexer actually scans. It equals `workspace_root`
unless `monorepo_path` narrows it to a subdirectory.

---

## Scan Policy Merging

`source_import_scan_policy()` merges `ignored_paths` from the import request
into the canonical source scan policy override dict. The merged paths are
deduplicated and stored as `excluded_path_prefixes`.

This is how Studio's "ignored paths" field on the import form reaches the
scan step without duplicating scan policy logic in the Studio handler.

---

## Security Model

### Path containment

Both `monorepo_path` and the git clone directory are validated against their
parent roots using `Path.relative_to()`. Any path that would escape the
workspace root is rejected with `ValueError` before the filesystem is
touched.

### URL validation

For `git_repository`, the URL must:
- use `https://` or `http://` scheme
- have a non-empty `netloc`
- have no `username`, `password`, or `@` in `netloc`
- have a non-empty path segment

### No auth in OSS

`auth_connector_id` always raises `ValueError` in OSS. Private repository
imports require a hosted product connector that supplies credentials at clone
time.

### Ignored path sanitization

`safe_scan_relpath()` is applied to every entry in `ignored_paths` before
merging. Entries that escape the root, contain `..`, or are absolute paths
are silently dropped.

---

## Clone Directory and `MOZAIKS_SOURCE_IMPORTS_PATH`

Git clones are placed under a configurable root:

```
MOZAIKS_SOURCE_IMPORTS_PATH  (default: generated/source_imports)
```

If the env var is unset, the path resolves relative to the repo root detected
from `__file__`. Each app gets its own sub-directory:

```
generated/source_imports/{safe_app_id}/{safe_repo_slug}/
```

An existing clone directory is removed and recreated on each import request.
This ensures a fresh clone without requiring cleanup between runs.

---

## Public Payload Redaction

`public_source_import_result()` strips the absolute `workspace_root` and
`selected_root` from results surfaced through Studio's job API. The public
payload reports `workspace_root_present` and `selected_root_present` as
booleans instead.

The `import_root` key is also stripped from `metadata`. This prevents
operator-specific filesystem layout from leaking into public job status
payloads.

---

## Relationship to App Intelligence Index Jobs

Studio creates an `AppIntelligenceIndexJob` record before calling the source
import resolver. The resolved `SourceImportResult` is stored on the job as
`import_result` (redacted for public views) and `workspace_root`.

The indexer reads `workspace_root` from the job when it starts the scan
phase. If `workspace_root` is absent, it falls back to
`import_result.selected_root`.

---

## Implementation Map

| Concern | File |
|---|---|
| Request/result schemas | `mozaiksai/control_plane/source_import.py` — `SourceImportRequest`, `SourceImportResult` |
| Main resolver | `mozaiksai/control_plane/source_import.py` — `resolve_source_import()` |
| Scan policy merge | `mozaiksai/control_plane/source_import.py` — `source_import_scan_policy()` |
| Public payload redaction | `mozaiksai/control_plane/source_import.py` — `public_source_import_result()` |
| Path safety helper | `mozaiksai/core/app_context/__init__.py` — `safe_scan_relpath()` |
| Index job record | `mozaiksai/control_plane/app_intelligence_jobs.py` — `AppIntelligenceIndexJob` |
| Studio handler | `mozaiksai/hosts/studio.py` — source import and index endpoints |
| Tests | `tests/test_source_import.py` |

---

## Contributor Rules

- Do not add provider-specific clone mechanics to OSS. Auth credential
  resolution belongs in the hosted product's connector layer.
- Do not allow `workspace_root` or absolute paths in public job payloads.
  Use `public_source_import_result()` before returning results to clients.
- Path containment checks (`relative_to`) must be applied to every
  operator-supplied path before any filesystem operation.
- `ignored_paths` entries must always be sanitized through
  `safe_scan_relpath()` before merging into the scan policy.
