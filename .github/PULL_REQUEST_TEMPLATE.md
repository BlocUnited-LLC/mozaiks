<!--
  Thanks for contributing to Mozaiks! Please fill out this template so
  maintainers have what they need to review efficiently. See CONTRIBUTING.md
  for the full contribution path.
-->

## Related issue

<!-- Link the issue this PR addresses, e.g. "Closes #123". Write "N/A" if none. -->

## Summary

<!-- What does this PR change, and why? Keep the diff focused on one thing. -->

## Tests run

<!--
  List the commands you ran and their result, e.g.:
  `pytest tests/test_foo.py -v` -> 12 passed
  For docs-only changes: `python -m mkdocs build --strict` -> succeeded
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
