# Retired launchers and generated snapshots

Reviewed against `471721b6ca4bb6ca6d13f76ef82b032159e340a3` on 2026-11-10.
This is a source-tree retirement, not a runtime cleanup or a history rewrite.

## Decision and supported entrypoints

- Retire `scripts/startapp.ps1`, `scripts/cleanse.ps1`, and root `start-dev.ps1`
  as one obsolete chain. `startapp.ps1` always requested the full cleaner,
  stopped all running Docker containers, and could shut down WSL. The full
  cleaner removed machine-wide Docker containers/images and pruned volumes;
  its default path also cleared database documents. These are not safe startup
  prerequisites in a shared development environment.
- `start-dev.ps1` also invoked the absent `mozaiksai.cli` module and targeted
  `app/` as the frontend rather than the current `web_shell/` host.
- Use [run-studio.ps1](../../scripts/run-studio.ps1) for the existing combined
  development launcher, or [run-backend.ps1](../../scripts/run-backend.ps1)
  and [run-frontend.ps1](../../scripts/run-frontend.ps1) individually. Their
  existing [runtime artifact cleaner](../../scripts/clean-runtime-artifacts.ps1)
  remains unchanged. Coordinate any launch or cleanup with the runtime owner.
- Root [start-app.ps1](../../start-app.ps1) is a separate Compose launcher and
  remains unchanged. [clear_collections.py](../../scripts/clear_collections.py)
  remains an explicitly invoked operator utility with the same behavior;
  only its obsolete caller documentation changed.

Tracked-source references outside the retired chain were limited to the
utility's comments and a historical changelog entry. The changelog remains
historical evidence. A bounded local task/shortcut/config check and a sibling
workspace source scan found no additional callers; this is not a guarantee
about external installations or unexamined shortcuts. Existing local data,
logs, environments, worktrees, containers, and shared-checkout edits are not
part of this change.

## Removed generated output

Only the three tracked files below were removed. The small ZIP is a generated
sample app, not a canonical fixture. The two acceptance `artifact.zip` files
are JSON graph/source snapshots despite their suffixes, totaling 19,634,334
bytes. Current `tests/test_workspace_app_intelligence_acceptance.py` creates
its own content store under pytest temporary paths instead of reading them.
Root `generated/` and `generated_artifacts/` are ignored and guarded against
tracking; intentional fixtures under `tests/fixtures/` and
`web_shell/playwright/fixtures/` remain allowed. No age-based deletion is used.

## Exact recovery inventory

All blobs below are retained by baseline commit
`471721b6ca4bb6ca6d13f76ef82b032159e340a3` and its history.

| Removed path | Git blob | Last path-changing commit |
| --- | --- | --- |
| `scripts/startapp.ps1` | `03ca529a85810d8a72a3608af424f1c1a53cbc78` | `7e42724d4024cccd748e028bc18a4db6e8397979` |
| `scripts/cleanse.ps1` | `7302b417b8eac83856e4ea303665110dc3f08659` | `ee05b60851b5b54cfc3d6687281e6c7d47b3017b` |
| `start-dev.ps1` | `8bfdb997e7f44e62f8d6562c69a05c34eb980248` | `a27d7c45277efae150659ba04ac2233a8fb20110` |
| `generated/app_intelligence/sample_app/20260913035003/artifact.zip` | `e6ae8dd815c82a044d209fc4cbace17d3b65fc77` | `d5e7e13fab23449fa9399db76e6f8a62d9ec101f` |
| `generated_artifacts/workspace_app_intelligence_acceptance/app_context_graph-app_context_graph/artifact.zip` | `cf672e0b732cb8ccf69fbb87a3501f1b9a0813b3` | `510f18a48bd24569fb9356530cdc499ec116a549` |
| `generated_artifacts/workspace_app_intelligence_acceptance/source_context_bundle-source_context_bundle/artifact.zip` | `2924cbedf359d577f92e6957aef1b1e5bc5a0738` | `510f18a48bd24569fb9356530cdc499ec116a549` |

For text inspection, use `git show <baseline-commit>:<removed-path>`. To recover
exact files for inspection, use `git archive --format=zip --output=<new-inspection-archive.zip> <baseline-commit> -- <removed-path>`
with the baseline and a path above. Extract only into a separate unused
inspection directory. Do not execute recovered launchers or copy snapshots
into an active workspace. No duplicate backup or destructive cleanup is needed.

## Regression coverage

`tests/test_no_stale_repo_artifacts.py` checks the tracked index, rejects both
output roots and all three retired launcher paths, checks actual Git ignore
behavior, allows intentional fixture locations, and verifies the preserved
launchers do not reference the retired chain. These checks inspect source and
Git state; they do not start services or execute cleanup scripts.
