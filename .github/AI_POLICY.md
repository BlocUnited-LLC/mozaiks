# AI Policy

Mozaiks is a framework for building AI-native software, and much of it is
itself built with AI assistance. Contributors who use coding agents are
welcome here. What we ask in return is simple and applies to everyone:

**You are responsible for everything you publish** — code, issue descriptions,
pull request bodies, comments, and reviews. A human has to stand behind the
submission and be able to discuss it.

This standard does not change based on how a contribution was produced. It
applies to a first-time contributor using Copilot, to a maintainer running
Claude Code, and to the automated agents that open PRs on this repository.

## This applies to maintainers too

A large share of the commits on `main` are agent-authored. Maintainer agents
push branches under `cc/` (Claude Code) and `codex/` (Codex) and open pull
requests the same way anyone else does.

Those PRs are held to this policy, not exempted from it. A named human
maintainer is accountable for every agent-authored PR merged here: they are
responsible for the diff, they answer questions about it, and "the agent wrote
it" is not an explanation. If we could not accept that answer from you, we
should not accept it from ourselves.

We say this explicitly because a policy that governs only outside contributors
is not a standard, it is a toll gate.

## Code: ownership and licensing

If you used AI to generate code, understand what it does and test it before
submitting. If your tests were also AI-generated, check that they actually
exercise the behavior. Tests written alongside the code they test have a habit
of asserting whatever the code happens to do, including the bug.

Mozaiks is [MIT licensed](https://github.com/BlocUnited-LLC/mozaiks/blob/main/LICENSE).
There is no CLA and no rights assignment; contributions are certified under the
[Developer Certificate of Origin](https://github.com/BlocUnited-LLC/mozaiks/blob/main/DCO.md),
which you sign per commit with `git commit -s`.

That certification is a statement about provenance, and AI-assisted work is
where provenance is easiest to lose track of. A model can reproduce training
data closely enough to matter, so before signing off on generated code, satisfy
yourself that you are not carrying in third-party material you have no right to
contribute. If a generated block looks like it came from somewhere specific, it
may have.

## Know which repository your change belongs in

This is the failure mode most specific to Mozaiks, and coding agents walk into
it constantly.

`mozaiks` is the open framework: runtime, contracts, ports, capability packs,
module archetypes, CLI, and Studio. BlocUnited's hosted product is a separate,
private codebase that implements those contracts — the concrete payment
provider integration, hosted billing, plan tiers, and production operations.

The seam is `mozaiksai/core/ports/`. The framework defines the Protocol and
ships a default or no-op adapter; a product implements the real one. The
`EntitlementPort` is the clearest example: this repository owns the contract,
the `EntitlementResult` shape, the fail-closed convention, and the
`NoOpEntitlementAdapter`. It deliberately does not own anything that resolves a
real customer's paid subscription.

Given an issue that mentions billing or entitlement, an agent will cheerfully
propose a full grant store, a payout ledger, or a provider webhook handler, and
none of that belongs here. If your change adds provider-specific commercial
logic to this repository, it is in the wrong repository, however good the code
is.

Read [OSS_PUBLICATION_POLICY.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/OSS_PUBLICATION_POLICY.md)
before starting anything that touches payments, entitlement, provider
execution, deployment, DNS, credentials, or production operations. MIT
publication is a one-way door, and the pull request template's boundary
checkbox is a prompt to think, not a box for an agent to tick.

When you are unsure which side of the line a change falls on, ask in the issue
before writing the code. That question is always welcome and never a bother.

## Issues, pull requests, and discussion

If you open an issue, understand the problem well enough to describe it
clearly. If AI helped you draft the description, edit it so it reflects your own
understanding before posting.

If you open a pull request, be able to explain the change — in the body, and in
response to review questions. Verify specific claims against the actual diff:
file paths, function names, error messages, and especially "I ran the tests."
A PR body that describes behavior the diff does not have is worse than a PR
body with no description at all, because it costs a reviewer the time to
discover the mismatch.

**Be prepared to discuss and revise in your own words.** Pasting an agent's
reply back at a reviewer does not move the conversation forward, and it is
usually obvious.

Mentioning significant AI assistance in the PR body is appreciated. It is not
held against you; it helps a reviewer calibrate where to look.

If you want to share raw AI output in a comment, put it in a quote block, say
what it is, and add your own commentary on why it is relevant. Keep it short.

### A note on our issues

Many issues in this repository — particularly those labeled
`good first issue` — are written in unusual detail, with file and line
references and a suggested approach. That is deliberate: it lowers the barrier
for a genuine first contribution.

It also makes those issues easy to hand to an agent without ever reading the
surrounding code. Please read the code. The suggested approach in an issue is a
starting point written by someone who may have been wrong, and noticing that it
is wrong is a more valuable contribution than implementing it faithfully.

## Reviews

Reviews from anyone in the community are welcome, not just maintainers. AI
tools can help you read a large diff or spot patterns worth a closer look, but
**a review has to reflect that a person engaged with the code**.

A review reads as unverified AI output when it:

- restates the PR description back as findings instead of responding to the diff,
- praises choices in generic terms without pointing at specific lines,
- asks questions the diff answers directly, such as whether something is tested
  when the test file is right there,
- approves while leaving its own open question unresolved,
- reuses the same template across unrelated PRs.

Maintainers may hide a review showing clear signs of unverified AI content, and
will leave a comment explaining why. Reviews do not gate a PR — only a
maintainer's approval does — but a low-effort review can mislead a less
experienced contributor into believing a change was vetted when it was not.

## Non-native English speakers

AI is genuinely useful for writing in a second language, and we would rather
have your contribution in imperfect English than not at all. If you use AI to
polish your comments, make sure the result still says what you meant. If you
use it to translate, consider writing in your own language and including the
translation.

## Maintainer discretion

Maintainers may close or deprioritize contributions where the author cannot
explain the change, has not tested it, or cannot engage with review questions.

The bar is the same regardless of how the work was produced. We only need to be
able to trust that a person stands behind it.

Questions about any of this are welcome in
[Discord](https://discord.gg/Qnsywad9kp) or on the issue itself.
