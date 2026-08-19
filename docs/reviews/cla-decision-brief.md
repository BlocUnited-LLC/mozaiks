# CLA Decision Brief

**Status:** open decision, prepared for legal review
**Prepared:** 2026-08-19
**Audience:** BlocUnited leadership, and outside counsel engaged to review or draft the agreement

> This document was prepared by a coding agent to make a legal engagement short
> and cheap. It is **not** legal advice, and nothing in it should be treated as
> a drafted agreement. Its purpose is to state the facts and the decision
> clearly enough that counsel can answer in one pass.

---

## 1. The decision

Should contributions to the open-source `mozaiks` repository require a
Contributor License Agreement, and if so, of what kind?

Today there is none. A Developer Certificate of Origin (DCO) is in flight
(PR #337) and is a different instrument: it records a contributor's
representation that they had the right to submit their work, but it transfers
no rights and does not enable relicensing.

## 2. Why this is being asked now

The value of a CLA is almost entirely **optionality**: it is what makes it
possible to relicense the project later without needing agreement from every
past contributor. That option narrows as contributions accumulate and cannot be
reconstructed after the fact.

The repository is early enough that outside contribution is still a small
fraction of the codebase. That is what makes this worth deciding now rather
than later. The cost is not about work already merged; it is that adopting a
CLA gets more expensive with every contribution accepted without one.

## 3. Current legal posture

- **License:** MIT (`LICENSE`), applied to the whole repository.
- **Contributions:** accepted with no CLA and no DCO to date. Contributors
  retain their copyright; the project relies on the implicit inbound=outbound
  convention that a contribution to an MIT project is offered under MIT.
- **Publication policy:** `OSS_PUBLICATION_POLICY.md` treats MIT publication as
  a deliberate one-way door and governs what may be published at all.
- **Commercial position:** BlocUnited operates a separate, private hosted
  product built on this framework. MIT already permits that use; **no CLA is
  needed for the hosted product to build on contributed code.** This is worth
  stating plainly because it is the most common reason companies believe they
  need a CLA, and it does not apply here.

## 4. What the CLA would actually be for

The single question that determines whether this is worth doing:

> **Does BlocUnited want to preserve the ability to relicense `mozaiks` away
> from MIT in the future?**

Scenarios where that option has value:

- Moving to a source-available or reciprocal license (BSL, SSPL, AGPL) to
  prevent a cloud provider or competitor from reselling the framework as a
  hosted service.
- Dual licensing — offering a commercial license alongside the open one.
- An acquisition or funding process where a buyer's diligence asks who holds
  rights to the codebase.

Scenarios where it does not:

- Using contributed code in the hosted product. **Already permitted by MIT.**
- Protecting the proprietary adapters, MozaiksPay, hosting, or operations.
  Those live in a separate private repository and are unaffected.

## 5. Options

| Option | What it does | Cost |
|---|---|---|
| **DCO only** (current direction) | Per-commit certification of provenance. No rights transfer. | Effectively zero. One flag: `git commit -s`. |
| **Individual CLA (ICLA)** | Contributor grants BlocUnited a broad license, typically including the right to sublicense/relicense. Contributor retains copyright. | Signing gate before any PR merges. Some contributors decline on principle. |
| **ICLA + Corporate CLA (CCLA)** | Adds a company-level agreement for contributors submitting on an employer's behalf. | As above, plus corporate signatories are slower. |
| **Copyright assignment** | Contributor transfers copyright outright. | Strongest for the project, most objectionable to contributors. Not recommended. |
| **DCO + CLA** | Both. Several large projects do this. | Redundant friction; probably unnecessary here. |

The common starting point for options 2 and 3 is the Apache Software
Foundation's ICLA/CCLA, which many companies adapt. **Adapting a template is
exactly where counsel is needed** — the entity, jurisdiction, governing law,
patent grant scope, and the interaction with MIT all have to be right, and a
CLA copied from another project (for example AG2's, which is drafted for a
different entity and for Apache-2.0) should not be used.

## 6. Questions for counsel

1. Given MIT outbound and the intent to preserve relicensing optionality, is an
   ICLA sufficient, or is a CCLA also needed given that contributors may submit
   from employer-owned time?
2. Should the grant include an explicit patent license and defensive
   termination clause?
3. What governing law and venue are appropriate for BlocUnited LLC?
4. Does accepting contributions under DCO in the interim create any obstacle to
   introducing a CLA afterward?
5. Is any disclosure required regarding AI-assisted contributions, given that a
   substantial share of the codebase is agent-authored with human sign-off?
   See `.github/AI_POLICY.md`.
6. Should the agreement apply only to contributions merged after adoption, and
   is anything needed to make that boundary explicit?

## 7. Recommended sequencing

1. **Ship the DCO now** (PR #337). It costs nothing, provides immediate
   provenance value, and does not foreclose a CLA. Question 4 above confirms
   this assumption before relying on it.
2. **Engage counsel** with this brief. The ask is narrow: review or adapt an
   ICLA (and CCLA if advised) for BlocUnited LLC against an MIT-licensed
   project.
3. **Enable the gate** once text exists, applying to contributions from that
   point forward — see the implementation appendix.
4. **Decide whether the DCO stays** alongside the CLA or is retired.

## 8. What it will cost

Honest accounting, so this is not adopted on optimism:

- **Contributor friction.** Every contributor signs before their first merge.
  This lands hardest during the current phase, where the project is actively
  trying to attract its first outside contributors.
- **Attrition.** A minority of open-source contributors decline CLAs on
  principle, particularly ones permitting relicensing. Some contributions will
  be lost. This is a real cost, not a hypothetical one.
- **Any pull request open when the gate goes live** needs a signature before
  merging, from a contributor who has already been waiting on review. Clear the
  queue before switching it on.
- **Ongoing.** Someone must maintain the signature record and respond when the
  bot blocks a PR.

None of these are reasons not to do it. They are reasons to decide
deliberately rather than by default, and to merge the open PRs before the gate
goes live.

---

## Appendix: implementation

The standard tool is [cla-assistant.io](https://cla-assistant.io) — free for
open-source projects, stores signatures against a gist, and posts a status
check on each PR. Setup, once signed text exists:

1. Sign in to cla-assistant.io with the GitHub account that administers
   `BlocUnited-LLC/mozaiks` and authorize the app. **This step requires a human
   with org admin rights; it cannot be scripted.**
2. Create a public gist containing the agreed CLA text.
3. In cla-assistant, link the gist to the repository and configure the check.
4. Add the resulting `license/cla` check to the branch protection rules for
   `main` as a required check.
5. Update `CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE.md` to reference
   the CLA, and reconcile the DCO wording in `.github/AI_POLICY.md` and
   `DCO.md`.
6. Add a `welcome-first-pr` note and a `needs-cla` canned reply under
   `.github/review/replies/`, matching the existing set.

Steps 5 and 6 are ordinary repository work and can be prepared in advance of
the legal text. Steps 1 through 4 are gated on a human decision and a signed
document, and should not be started before then.
