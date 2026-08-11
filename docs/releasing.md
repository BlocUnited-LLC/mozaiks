# Releasing

> **RELEASES ARE CURRENTLY DISABLED.**
>
> GitHub Releases and PyPI publication have been intentionally paused pending
> completion of the pre-release checklist below.  Do not push a version tag
> until every P0 item is verified.
>
> **Release gate status (verified August 2026): NOT PROTECTED.**
> The `release` GitHub environment has `protection_rules: []` — no required
> reviewers.  Any tag push would have proceeded to publication automatically.
> As a code-level guard, the tag trigger in `.github/workflows/release.yml`
> has been disabled.  Only a manual `workflow_dispatch` with
> `confirm_release: "release-confirmed"` can proceed.  This guard remains
> active until required reviewers are configured and the tag trigger is
> re-enabled.
>
> To check the current gate status:
> ```bash
> gh api repos/BlocUnited-LLC/mozaiks/environments/release
> # Must show protection_rules with at least one reviewer before re-enabling.
> ```

Mozaiks has a tag-driven release workflow (currently disabled — see above).

Keep versions pre-`1.0.0` until the repo contracts, CLI UX, and Studio-first
builder flow settle. A `0.x` release is the honest signal to users that
breaking changes can still happen.

---

## Pre-Release Checklist

Complete every **P0** item before pushing a release tag.  P1 items should be
resolved before a stable 1.0 release.

### P0 — Must Be Done Before ANY Release

- [ ] **GitHub environment `release` has required reviewers configured.**
  **Current status: NOT PROTECTED** (`protection_rules: []`, verified August 2026).
  The release workflow gate (`environment: name: release` in
  `.github/workflows/release.yml`) blocks publication only when GitHub
  Settings → Environments → `release` → Required reviewers lists at least
  one human reviewer.  The tag trigger is currently disabled as a code-level
  guard.  Before re-enabling it:
  1. Add reviewers: GitHub Settings → Environments → release → Required reviewers.
  2. Verify: `gh api repos/BlocUnited-LLC/mozaiks/environments/release`
     — `protection_rules` must be non-empty.
  3. Uncomment the `push.tags` trigger in `.github/workflows/release.yml`.

- [ ] **Run the local release-candidate audit.**
  Execute the pre-release audit script (see [Release-Candidate Audit Command](#release-candidate-audit-command) below)
  and confirm it exits 0:
  ```bash
  python scripts/run_release_audit.py
  ```

- [ ] **Governance guardrails pass on main.**
  ```bash
  python scripts/governance_guardrails.py --all --errors-only
  ```

- [ ] **Package content guard passes on built artifacts.**
  ```bash
  python -m build
  python scripts/package_content_guard.py dist/*.whl dist/*.tar.gz
  ```

- [ ] **CHANGELOG.md has a dated release entry** (not just `## Unreleased`).
  Move all `Unreleased` entries to a new `## <version> - YYYY-MM-DD` section
  and leave a fresh empty `## Unreleased` header.

- [ ] **`mozaiksai/version.py` matches the planned Git tag.**
  The release workflow validates this and fails if they disagree.

- [ ] **`factory_app/app/brand/realm-export.json` contains no production values.**
  Verified clean (August 2026): contains only `realm`, `enabled`, `displayName`,
  and generic Keycloak settings.  Re-verify if the file changes before release.

- [ ] **All CI checks pass on main.**
  Confirm the test, lint, secret-scan, dependency-audit, governance, and
  frontend jobs are green.

### P1 — Resolve Before 1.0

- [ ] **PyPI trusted publishing is configured.**
  The `mozaiks` PyPI project must trust this GitHub repository and the
  `release.yml` workflow file.  Without this, the `publish-pypi` job fails
  after the GitHub Release is created.

- [ ] **Documentation site is up to date.**
  Confirm `mkdocs build --strict` passes with no warnings.

- [ ] **ADR 0002 has been reviewed by a second engineer.**
  `docs/adr/0002-appgenerator-baseline-strategy-oss.md` records the intentional
  OSS publication of the AppGenerator baseline strategy.

---

## Release-Candidate Audit Command

Run this locally before tagging any release.  It chains governance, build,
package inspection, smoke install, and resource verification:

```bash
python scripts/run_release_audit.py
```

The script lives at `scripts/run_release_audit.py` (see source for details).
It builds a wheel into a temp directory, runs the content guard, smoke-installs
into a clean venv, and verifies that Factory resources resolve from the install.

---

## Release Steps

The release entrypoint is:

1. bump `mozaiksai/version.py`
2. commit the version change
3. push a matching Git tag

Example:

```bash
git checkout main
git pull
# edit mozaiksai/version.py -> __version__ = "<version>"
git add mozaiksai/version.py
git commit -m "Release <version>"
git tag v<version>
git push origin main --tags
```

## What The Release Workflow Does

The GitHub Actions workflow at `.github/workflows/release.yml` runs when a tag
matching `v*` is pushed.

It:

1. verifies the Git tag matches `mozaiksai.version.__version__`
2. builds the shared Studio frontend shell
3. builds the Python sdist and wheel
4. runs `twine check`
5. installs the built wheel into a clean virtualenv
6. smoke-tests the installed CLI and packaged resources
7. creates a GitHub release with attached artifacts
8. publishes the distributions to PyPI

## PyPI Setup Requirement

The workflow is configured for GitHub-to-PyPI trusted publishing.

The `mozaiks` PyPI project must trust this GitHub repository and the
`release.yml` workflow.

Until that is configured on the PyPI side, the `publish-pypi` job will fail
even if the build and GitHub release steps succeed.

## Documentation Impact

Public install docs should present `pip install mozaiks` followed by
`python -m mozaiks ...` so PATH mechanics stay out of the main onboarding flow.
The `mozaiks` command can be mentioned as an optional shortcut only.
Keep source-checkout setup separate as the framework/developer mode.

## Notes

- The package version is now sourced from `mozaiksai/version.py`.
- `pyproject.toml` reads that value dynamically during builds.
- The CLI `--version` output and FastAPI host version metadata now use the same
  version source.
