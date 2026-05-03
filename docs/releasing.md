# Releasing

Mozaiks now has a tag-driven release workflow.

The release entrypoint is:

1. bump `mozaiksai/version.py`
2. commit the version change
3. push a matching Git tag

Example:

```bash
git checkout main
git pull
# edit mozaiksai/version.py -> __version__ = "1.0.1"
git add mozaiksai/version.py
git commit -m "Release 1.0.1"
git tag v1.0.1
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

Before the first public release, configure the `mozaiks` project on PyPI to
trust this GitHub repository and the `release.yml` workflow.

Until that is configured on the PyPI side, the `publish-pypi` job will fail
even if the build and GitHub release steps succeed.

## Documentation Impact

The packaging and release plumbing is now in place, but the public install
story should only change after the first successful PyPI publish.

Until that happens:

- `README.md`, `docs/getting-started.md`, and `docs/install-modes.md` should
  keep the repo-first bootstrap path as the default.
- `pip install mozaiks` should remain documented as a future public path, not
  the primary first-run path.

After the first successful PyPI release:

- update the first-run docs to promote `pip install mozaiks`
- keep the repo checkout path as the framework/developer mode

## Notes

- The package version is now sourced from `mozaiksai/version.py`.
- `pyproject.toml` reads that value dynamically during builds.
- The CLI `--version` output and FastAPI host version metadata now use the same
  version source.
