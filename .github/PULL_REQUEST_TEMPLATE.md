<!--
  Thanks for contributing to Mozaiks! Please fill out this template so
  maintainers have what they need to review efficiently. See CONTRIBUTING.md
  for the full contribution path.

  Before implementing a bug fix, sync with current main and confirm the issue
  still reproduces. Mozaiks moves quickly, and an older report or suggested
  implementation may already have been superseded.

  Using an AI coding agent? That is welcome — see .github/AI_POLICY.md. The
  short version: make sure this description matches what the diff actually
  does, and that you can explain the change in review.

  Mozaiks does not run a bounty program; issues carry no payment. See
  CONTRIBUTING.md.
-->

## Related issue

<!-- Link the issue this PR addresses, e.g. "Closes #123". Write "N/A" if none. -->

## Current-main verification

- [ ] I synced/rebased onto current `main` before implementing this change.
- [ ] For a bug fix, I confirmed the reported failure still reproduces on current `main`, or I explained below why reproduction is not applicable.

<!--
  For bug fixes, briefly state how you reproduced the problem on current main.
  If the original issue no longer reproduces, do not carry forward an obsolete
  local fix; comment on the issue/PR so maintainers can close it as superseded.
-->

## Summary

<!-- What does this PR change, and why? Keep the diff focused on one thing. -->

## Tests run

<!--
  List the commands you ran and their result, e.g.:
  `pytest tests/test_foo.py -v` -> 12 passed
  For docs-only changes: `python -m mkdocs build --strict` -> succeeded
-->

## AI assistance (optional)

<!--
  If an AI tool did significant work here, a one-line mention helps reviewers
  calibrate where to look. It is not held against you — a good chunk of this
  repo is agent-authored. Delete this section if it does not apply.
-->

## Screenshots (if applicable)

<!-- Include before/after screenshots or a short clip for any UI-visible change. Delete this section if not applicable. -->

## Boundary confirmation

- [ ] This change stays within the open-source `mozaiks` repository and does not introduce private hosted-product logic, credentials, or internal-only URLs.

## Governance check

- [ ] This is an ordinary change with no new public schema, public workflow/prompt family, provider mutation path, authority bypass, eval artifact, learned optimization, or cross-customer intelligence.
- [ ] If this adds or changes a public contract, the contract is classified and versioned.
- [ ] If this touches a one-way-door area from `OSS_PUBLICATION_POLICY.md`, a short ADR is included.
- [ ] If this touches permissions, secrets, provider execution, payments, deployment, DNS, or production operations, the authority and review path are explicit.