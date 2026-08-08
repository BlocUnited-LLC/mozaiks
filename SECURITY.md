# Security Policy

Mozaiks is pre-launch software (see [AGENTS.md](AGENTS.md)). We still take
security reports seriously and ask that you report vulnerabilities privately
so they can be assessed before any public disclosure.

## Reporting a Vulnerability

Please use GitHub's private vulnerability reporting for this repository
instead of opening a public issue or pull request:

1. Go to the [Security tab](https://github.com/BlocUnited-LLC/mozaiks/security).
2. Select **Report a vulnerability**.
3. Describe the issue, including affected version/commit, reproduction steps,
   and potential impact.

This opens a private advisory visible only to you and the repository
maintainers, who will respond and coordinate a fix and disclosure timeline
with you.

Do not include exploit details, credentials, or other sensitive information
in a public GitHub issue, discussion, or pull request.

## Supported Versions

This project is pre-1.0 and does not yet maintain long-term-supported
release branches. Security fixes are applied to the latest release on
`main`. If you are running an older version, please upgrade before or
alongside reporting an issue.

## Scope

This policy covers the `mozaiks` OSS repository (runtime, platform, Studio,
CLI, and factory workflows). It does not cover privately hosted products
built on top of Mozaiks; report issues in those products to their own
maintainers.
