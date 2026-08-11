# ADR 0001: Public Architecture And OSS Strategy Boundary

Date: 2026-08-10

Status: accepted

## Decision

Mozaiks has one canonical application model. `factory_app` is the OSS reference
canonical app, and proprietary applications such as App Zero should consume the
same public framework contracts instead of maintaining a private framework fork.

Mozaiks uses AG2 as the generic agent execution substrate. Mozaiks owns the
canonical app, workflow, artifact, validation, runtime, and product integration
contracts around AG2.

The OSS baseline remains capable of understanding, generating, validating,
discovering, and refining one application. Operator/learned intelligence derived
from many applications, evals, production outcomes, or commercial operations is
publication-review/private-by-default.

Managed capabilities use public replaceable contracts. MozaiksPay may be the
recommended/default payment capability, but it is not mandatory and does not
make BlocUnited payment internals part of OSS.

## Reason

The project needs a durable boundary before 1.0 so rapid AI-assisted
development does not accidentally publish irreversible MIT contracts, fork the
framework privately for App Zero, or make the OSS baseline depend on BlocUnited
services.

## Alternatives Considered

- Treat App Zero as a special proprietary fork: rejected because it would weaken
  the OSS framework and prevent community improvements from benefiting the
  commercial application through normal dependency upgrades.
- Move baseline generation/refinement strategy out of OSS: rejected because it
  would make the public framework less useful, less trustworthy, and less
  self-hostable.
- Publish all future strategy improvements automatically: rejected because
  customer-derived datasets, eval evidence, learned rankings, and hosted
  operational intelligence are different from generic one-application framework
  capability.
- Make MozaiksPay mandatory: rejected because canonical apps should depend on a
  replaceable payment capability contract rather than one hosted
  implementation.

## Consequences

- OSS documentation and tests must protect public framework contracts.
- App Zero should request new public framework seams when it needs generic
  framework behavior.
- Operator-specific provider execution, production credentials, money movement,
  commercial policy, and many-app learned intelligence remain application or
  operator concerns.
- AG2 upgrades require compatibility review because upstream AG2 capabilities
  are not automatically Mozaiks public APIs.

## Reversibility

High risk: public canonical app contracts, AG2 adapter behavior, and MIT
published baseline generation/refinement strategy become difficult to retract
once third-party apps and App Zero consume them.

## Affected Invariants

- Public Framework Contracts Stay Provider-Neutral
- Agents Produce Candidates; Deterministic Code Validates and Promotes
- Public Schemas and Contracts Are Classified and Versioned
- Generic App Intelligence Can Be OSS; Multi-App Learned Intelligence Requires Review
- Mozaiks App Dogfoods Public Framework Contracts
- Operator Capabilities Are Explicitly Separated

## OSS Boundary

Open interface with reviewed implementation for managed/operator capabilities.
Keep OSS for canonical contracts, baseline generation/refinement/discovery,
validators, App Intelligence for one app, provider-neutral contracts, and AG2
adapters. Publication review is required for learned/operator assets.

## Validation

- MkDocs build or equivalent documentation validation.
- Governance guardrails.
- Public-contract tests when a code change adds or changes a contract.
- ADR review before publishing new one-way-door strategy, provider, authority,
  schema, eval, or learned-intelligence surfaces.
