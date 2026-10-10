# Releasing Mozaiks

> **Public release hold:** Do not tag, dispatch the Release workflow, publish to
> PyPI or TestPyPI, or create a GitHub release until the operator explicitly
> authorizes the exact OSS/App Zero commit pair after matching live acceptance.
> The workflow and this page prepare that decision; neither grants release
> approval.

Mozaiks remains pre-1.0. Version `0.2.0` is planned as the first **currently
installable** PyPI release. The PyPI project presently lists no distribution
files. Earlier `0.1.x` filenames cannot be reused, and restoring them is not
needed for `0.2.0`.

## Release gates

Complete every item for the **exact** release candidate commit before a public
dispatch:

1. Merge release-impacting OSS work through reviewed PRs with required CI and
   DCO checks. Record each PR number and final merge SHA. Never push directly
   to `main`.
2. Pin App Zero to the exact OSS git SHA, run live acceptance against that pair,
   and retain evidence identifying both commits. A version requirement can be
   considered after publication; it does not replace the acceptance pin.
3. Confirm the candidate is the current `origin/main` commit and its **push**
   CI run succeeded. The `package` CI job installs the built wheel's
   `[acp-coding,e2b]` extras in its clean environment and verifies imports and
   dependencies without contacting either provider. It also runs the E2B
   adapter and teardown contract tests with the optional SDK installed, so
   missing-SDK skips cannot hide provider API drift. The Release workflow
   checks the candidate commit and push CI again before upload.
4. Run the local release-candidate audit against a throwaway MongoDB server on
   a non-default port. It covers governance, wheel and sdist build, package
   content, metadata, strict documentation, clean-wheel install, packaged
   resources, installed first run, and offline functional acceptance. Install
   local audit dependencies with `pip install -e ".[dev,docs]" build twine`.
   Do not point it at the development
   MongoDB server: the runtime uses fixed database names regardless of the URI
   database segment.
5. Move every release entry from `CHANGELOG.md`'s `## Unreleased` section into
   a dated `## 0.2.0 - YYYY-MM-DD` section immediately below it. Leave
   `## Unreleased` empty, with no headings or entries between those two
   sections. The dated section needs at least one release-note bullet, and the
   release workflow repeats the audit's strict documentation build. Confirm
   `mozaiksai/version.py` is `0.2.0` and any existing
   `v0.2.0` tag resolves to the same candidate commit.
6. Recheck `factory_app/app/brand/realm-export.json` for production values. Its
   localhost browser callback is a local-development seed. Confirm production
   Compose requires the separate operator-owned import and its validator; do
   not put deployment domains or secrets in the packaged realm.
7. Verify the live GitHub `pypi` environment has at least one required
   reviewer, **admin bypass disabled**, and deployments limited to protected
   branches. These protections were present on 2026-10-09; verify them again
   before dispatch. The workflow fails if they are removed. This is the human
   approval gate for public upload. The separate `release` environment also
   has a required reviewer, but the current workflow does not use it.
8. Have the PyPI project owner verify that `mozaiks` trusts
   `BlocUnited-LLC/mozaiks`, `.github/workflows/release.yml`, and the
   `pypi` GitHub environment for OIDC publication. PyPI account settings
   cannot be proven by the public package index. Trusted publisher setup is a
   **0.2.0 publication prerequisite**, not a later 1.0 task.
9. Review the exact final PR head, release diff, package contents, unresolved
   security issues, and the OSS/App Zero acceptance evidence. Obtain explicit
   operator authorization for the selected candidate and target.

The tag trigger remains disabled. A manual workflow dispatch is the only
entrypoint. It requires a full candidate SHA, a target-specific confirmation,
the exact current `main` checkout, a successful CI push run for that SHA, an
empty `Unreleased` section followed immediately by the dated candidate
changelog section, and a matching existing tag if one exists. It does
not validate hosted acceptance; the operator must verify item 2 before use.

### Local candidate audit

Run this from PowerShell or a POSIX shell in a clean checkout of the final
candidate:

```text
docker pull public.ecr.aws/docker/library/mongo:7
docker tag public.ecr.aws/docker/library/mongo:7 mongo:7
docker run --rm -d --name mozaiks-release-audit-mongo -p 127.0.0.1:27018:27017 mongo:7
python scripts/run_release_audit.py --mongo-uri mongodb://127.0.0.1:27018
docker stop mozaiks-release-audit-mongo
```

The first two commands pull MongoDB through the same public mirror used by CI
and tag it locally for the audit command; they avoid anonymous Docker Hub
limits. If the local pull fails transiently, rerun it. CI uses the bounded
retry helper `scripts/ci/pull_public_image.sh`.

The audit script is `scripts/run_release_audit.py`. Its first-run smoke starts
both platform and Studio hosts and checks readiness and shell configuration
from the installed wheel. A passing audit on a different SHA is not release
evidence for the selected candidate.

The PyPI wheel and sdist contain the Python ACP and E2B adapters, but not the
repo-local `infra/docker/` image recipes or
`scripts/build_e2b_preview_template.py`. Operators enabling live ACP or hosted
E2B must build their runtime images/templates from the exact clean OSS source
commit and retain the source SHA and immutable image/template build identity.
Installing either Python extra does not provide a sandbox image or activate a
paid provider.

## Optional TestPyPI rehearsal

Only after the release hold is lifted for this candidate, configure a separate
TestPyPI trusted publisher for the same repository and workflow with GitHub
environment `testpypi`. Protect that environment with required reviewers,
admin bypass disabled, and protected-branch-only deployments. TestPyPI and
PyPI have separate accounts, projects, and
trusted-publisher settings; production credentials are not used for rehearsal.

After the owner verifies those settings, dispatch from `main` with the exact
candidate SHA:

```bash
gh workflow run release.yml --ref main \
  -f release_target=testpypi \
  -f candidate_sha=<40-character-main-sha> \
  -f confirm_release=testpypi-confirmed
```

This uploads the candidate wheel and sdist only to TestPyPI. It does not create
a GitHub release or upload to PyPI. Download the exact `mozaiks==0.2.0`
artifact from TestPyPI into a clean environment, compare its checksum with the
workflow artifact, and repeat the installed CLI/first-run smoke. The same
filename cannot be uploaded twice to TestPyPI; a failed or partial upload
requires inspection before another attempt.

## Public publication

After all release gates and any rehearsal are accepted, the operator may
dispatch the workflow from `main`:

```bash
gh workflow run release.yml --ref main \
  -f release_target=pypi \
  -f candidate_sha=<40-character-main-sha> \
  -f confirm_release=release-confirmed
```

The workflow builds and tests the candidate, waits on the protected `pypi`
environment, rechecks that the candidate is still current `main`, publishes
the built distributions to PyPI, then creates the GitHub release and tag at
that exact candidate SHA. The GitHub release body comes from the dated
changelog section. PyPI upload precedes GitHub release, so an upload failure
cannot leave a release announcement for an unavailable package. If PyPI
succeeds but GitHub release fails, inspect the immutable PyPI files and rerun
only the failed GitHub release job after correcting the cause.

Verify the final PyPI files, installed version and CLI, GitHub tag target,
release checksums, and release notes. Record the final OSS release SHA and
the accepted App Zero SHA with the live evidence.

## Later 1.0 work

- Obtain a second-engineer review of ADR 0002's AppGenerator baseline strategy.

These do not relax any `0.2.0` release gate above.
