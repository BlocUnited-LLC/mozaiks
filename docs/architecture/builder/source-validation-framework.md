# Source Validation Framework

The source validation framework runs framework-detected commands against a
workspace to measure source health before and after code changes. It is
intentionally opt-in: command execution requires `confirm_execution=True`.

Related documents:

- [App Intelligence Plane](../foundations/app-intelligence-plane.md)
- [Framework Detection](../foundations/framework-detection.md)
- [Source Import Contracts](source-import-contracts.md)

---

## Purpose

Validation answers: "Does this source still compile, lint, and test after the
last staged change?" It feeds into:

- The App Intelligence Plane's `AppContextVersion.validation_summary`
- Studio's App Intelligence panel health display
- The refinement harness as a post-edit quality signal

---

## Entry Points

### Against the current App Intelligence context

```python
result = await run_current_app_source_validation(
    app_id="my-app",
    allowed_kinds=["lint", "test"],
    confirm_execution=True,
)
```

Reads framework detection from the current `AppIntelligenceSnapshot` (or
the latest index job as a fallback) and resolves the workspace root from
the latest `AppIntelligenceIndexJob`.

### Against an explicit workspace

```python
result = run_app_source_validation(
    app_id="my-app",
    workspace_root="/path/to/workspace",
    framework_detection=detection_dict,
    confirm_execution=True,
)
```

Used by the refinement harness tool when it supplies a specific workspace
root and staged overlay files.

---

## Execution Model

```
framework_detection
    → plan_app_source_validation_commands()   # build ordered command plan
        → filter by allowed_kinds
        → reject long-running (dev, start)
        → reject unsafe command strings
        → reject disallowed executables
        → reject out-of-workspace working directories
        → cap at max_commands (default 4)
    → isolated workspace copy (tempfile)
    → apply overlay_files (staged changes)
    → execute planned commands in order
    → if no commands ran → fallback checks
```

### Isolation

By default (`copy_workspace=True`), the runner creates a temporary directory
and copies the workspace into it before executing any command. The copy
excludes:

- Cache and build output directories: `.cache`, `.git`, `.next`, `.pytest_cache`,
  `build`, `coverage`, `dist`, `node_modules`, `venv`, etc.
- Secrets and credential files: `.env`, `.env.local`, `.env.production`,
  `.npmrc`, `.pypirc`, `id_rsa`, `id_dsa`
- Compiled artefacts: `*.pem`, `*.key`, `*.pyc`, `*.pyo`

The temporary directory is cleaned up after the run regardless of outcome.

### Overlay files

`overlay_files` is a `dict[str, str]` of relative path → file content.
The runner writes these into the isolated copy before executing commands.
This allows the harness to validate staged changes without touching the
original workspace.

Limits: 200 files maximum, 2 MB total content. Paths are validated with the
same `_safe_relpath()` check used by source imports — no traversal out of
the workspace is possible.

---

## Command Security Model

Commands go through four checks before execution:

1. **Shell metacharacter rejection** — commands containing `;`, `|`, `&`,
   `` ` ``, `$`, `>`, `<`, `\r`, or `\n` are rejected outright.

2. **Argv parsing** — `shlex.split(command, posix=True)` must succeed.

3. **Executable allowlist** — only these executables may appear as `argv[0]`:

   ```
   composer   mypy     next     npm      npm.cmd
   php        pnpm     pnpm.cmd pytest   python
   python3    ruff     tsc      vite     yarn
   yarn.cmd
   ```

   `python` and `python3` are remapped to `sys.executable` at runtime.
   Other executables are resolved via `shutil.which()`.

4. **Working directory containment** — `working_directory` must resolve to a
   path inside the workspace root via `Path.relative_to()`.

Commands that fail any check are marked `skipped` with a machine-readable
`skip_reason`, not `failed`. This keeps aggregate status accurate.

---

## Command Kinds

| Kind | Runs by default | Included with `include_install` |
|---|---|---|
| `lint` | Yes | Yes |
| `typecheck` | Yes | Yes |
| `test` | Yes | Yes |
| `build` | Yes | Yes |
| `install` | No | Yes |
| `dev` | Never (long-running) | Never |
| `start` | Never (long-running) | Never |

Default kind order: install → lint → typecheck → test → build. Commands run
in this order when planned. Within a kind, higher-confidence candidates run
first.

---

## Fallback Checks

When no framework commands could be run (either none were planned or all were
skipped), the runner falls back to deterministic static checks:

| Check | What it validates |
|---|---|
| `json_manifest_parse` | `package.json`, `tsconfig.json`, `composer.json`, `*.schema.json` — valid JSON |
| `python_syntax` | All `*.py` files — `compile()` succeeds |
| `yaml_manifest_parse` | All `*.yaml` and `*.yml` files — `yaml.safe_load()` succeeds |

Fallback checks are also run when command results are all `skipped`.

Fallback checks never replace real framework validation. They provide a
minimum-viable health signal for apps where validation commands are
unavailable.

---

## Result Schema

```python
class AppSourceValidationResult(BaseModel):
    schema_version: "mozaiks.app_source_validation.v1"
    app_id: str
    validation_status: "passed" | "failed" | "skipped" | "warning"
    source: "app_intelligence_context" | "explicit_workspace"
    execution_mode: "isolated_workspace_copy" | "direct_workspace" | "not_executed"
    framework_detection_available: bool
    primary_framework_id: str | None
    primary_framework_label: str | None
    selected_kinds: list[str]
    planned_commands: list[AppValidationCommandPlanItem]
    command_results: list[AppValidationCommandResult]
    fallback_checks: list[AppValidationFallbackCheckResult]
    workspace_root_present: bool
    overlay_file_count: int
    started_at: str   # ISO 8601 UTC
    completed_at: str
    duration_ms: int
    warnings: list[str]
```

### Aggregate status rules

| Condition | Status |
|---|---|
| Any command or check `failed` | `failed` |
| Any command or check `passed`, none `failed` | `passed` |
| Any `warning`, none `passed` or `failed` | `warning` |
| All `skipped` | `skipped` |

---

## Environment During Validation

The runner sets two environment variables before executing commands:

- `CI=true` (unless already set)
- `MOZAIKS_APP_VALIDATION=1`

This signals to frameworks that they are running in a non-interactive
validation context. All other environment variables from the host process
are inherited.

---

## Skipping Execution Safely

When `confirm_execution=False` (the default), and runnable commands exist,
the result is returned with `execution_mode="not_executed"` and
`validation_status="skipped"`. The planned command list is still populated
so callers can inspect what would have run.

This makes it safe to call the planner to preview what validation would do
without actually executing anything.

---

## Refinement Harness Integration

The refinement harness calls source validation through
`factory_app/refinement_harness/tools/app_validation.py`. The tool:

1. Reads the current framework detection from App Intelligence.
2. Applies any staged overlay files from the harness context.
3. Calls `run_current_app_source_validation()` with `confirm_execution=True`.
4. Returns the result as a structured tool output for the LLM checkpoint.

The LLM uses validation results as a signal when deciding whether to accept
a staged change or route it to a review checkpoint.

---

## Implementation Map

| Concern | File |
|---|---|
| Main validation entry points | `mozaiksai/control_plane/app_validation.py` — `run_app_source_validation()`, `run_current_app_source_validation()` |
| Command planning | `mozaiksai/control_plane/app_validation.py` — `plan_app_source_validation_commands()` |
| Fallback checks | `mozaiksai/control_plane/app_validation.py` — `run_app_validation_fallback_checks()` |
| Result schema | `mozaiksai/control_plane/app_validation.py` — `AppSourceValidationResult` |
| Framework detection | `mozaiksai/core/app_context/framework_detection.py` |
| Refinement harness tool | `factory_app/refinement_harness/tools/app_validation.py` |
| Tests | `tests/test_app_source_validation.py` |

---

## Contributor Rules

- Do not add framework-specific logic to the validation runner. Executable
  detection and command emission belong in `framework_detection.py`.
- The allowlist in `_ALLOWED_EXECUTABLES` is the security boundary. Review
  any addition carefully — adding a general-purpose shell or script runner
  would defeat the containment model.
- Do not remove the workspace copy step for performance. The isolated copy
  ensures staged overlay files cannot be left behind in the real workspace.
- Do not pass `copy_workspace=False` from production callers. The direct
  workspace mode exists for testing only.
- All operator-supplied paths (overlay file keys, `working_directory`) must
  pass `_safe_relpath()` before any filesystem operation.
