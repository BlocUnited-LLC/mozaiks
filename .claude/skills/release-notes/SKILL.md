---
name: release-notes
description: Maintain Mozaiks release notes proactively. Use when changing released behavior, preparing a PyPI/GitHub release, bumping versions, modifying generated app scaffolds, changing public install/setup docs, or reviewing whether a change needs a changelog entry.
argument-hint: "[change or release task]"
disable-model-invocation: true
---

Complete this release-notes task: $ARGUMENTS

## Goal

Keep OSS Mozaiks release notes accurate without waiting until release day.

## When To Update `CHANGELOG.md`

Add an `Unreleased` entry when a change affects:

- CLI behavior or command output
- generated app scaffolds, scripts, or templates
- runtime, workflow, module, shell, event, or extension contracts
- packaging, PyPI, install, launch, or release CI behavior
- public docs that change how users install, run, or build with Mozaiks
- bug fixes or migration notes users would care about

Skip changelog entries for purely internal refactors, tests with no behavior
change, or small editorial fixes that do not affect user workflow.

## Entry Style

Use one concise bullet under the right section:

- `Added`
- `Changed`
- `Fixed`
- `Deprecated`
- `Removed`
- `Security`

Write from the user's point of view. Prefer:

- "Added generated app launch scripts for backend, frontend, and Studio."

Avoid:

- "Refactored helper function and changed three files."

## Release Prep Checklist

Before tagging:

1. confirm all release-impacting work is listed under `Unreleased`
2. move entries to `## <version> - <YYYY-MM-DD>`
3. create a fresh empty `## Unreleased` section
4. verify `mozaiksai/version.py` matches the tag
5. copy or summarize that version section into the GitHub Release notes

Keep private hosted-product release notes separate from this OSS framework
changelog unless the change is in this repo and affects public Mozaiks users.
