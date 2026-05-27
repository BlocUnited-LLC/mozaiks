# Releasing

Mozaiks has a tag-driven release workflow.

Keep versions pre-`1.0.0` until the repo contracts, CLI UX, and Studio-first
builder flow settle. A `0.x` release is the honest signal to users that
breaking changes can still happen.

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
