---
paths:
  - "CHANGELOG.md"
  - "docs/releasing.md"
  - "mozaiksai/version.py"
  - "pyproject.toml"
  - ".github/workflows/release.yml"
  - "mozaiks_cli/**"
  - "mozaiksai/**"
  - "factory_app/**"
  - "web_shell/**"
---

# Release Notes Rules

Use these rules whenever a change affects released Mozaiks behavior, public setup,
generated app output, framework APIs, CLI commands, runtime contracts, packaging,
or user-visible docs.

## Proactive Changelog Requirement

Update `CHANGELOG.md` in the same change when work has release impact.

Release-impacting changes include:

- new or changed CLI behavior
- generated app scaffold changes
- runtime, shell, workflow, module, or event contract changes
- packaging, PyPI, install, launch, or CI release behavior
- public docs that change the recommended user path
- bug fixes users would care about

Do not add changelog noise for:

- purely internal refactors with no public behavior change
- tests that only cover already-documented behavior
- typo fixes in non-user-facing docs

When unsure, add a concise `Unreleased` entry.

## Changelog Format

Keep the root `CHANGELOG.md` organized as:

- `## Unreleased`
- `## x.y.z - YYYY-MM-DD`

Use these subsections when relevant:

- `Added`
- `Changed`
- `Fixed`
- `Removed`
- `Security`

Entries should describe user-facing impact, not implementation trivia.

## Release Prep

Before tagging a release:

1. ensure `CHANGELOG.md` has all release-impacting changes under `Unreleased`
2. move those entries to `## <version> - <date>`
3. leave a fresh empty `## Unreleased` section at the top
4. verify `mozaiksai/version.py` matches the planned Git tag
5. use the changelog entry as the source for GitHub Release notes

Keep private hosted-product release notes separate from the OSS framework
changelog unless the change is in this repo and affects public Mozaiks users.
