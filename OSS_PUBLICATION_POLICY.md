# OSS Publication Policy

This repository is MIT-published framework infrastructure. Publication should be intentional because public MIT source is effectively a one-way door.

## Decision Rule

Open the ability to understand and build one application. Deliberately review what BlocUnited learns from understanding or operating many applications.

## Fast Path

Changes are normally safe for the public repository when they improve:

- runtime and canonical app contracts
- validators and deterministic contract checks
- generated-app portability and self-hosting
- baseline generation, discovery, and refinement
- generic App Intelligence that works from one app's source and public contracts
- public extension contracts
- provider-neutral deployment, secret, payment, data, and integration contracts
- local Studio, CLI, and developer tooling needed to operate OSS Mozaiks

These changes do not need an ADR unless they create a new public contract, expose a new authority surface, or publish substantial generation or operations intelligence.

## Publication Review Path

Use a short ADR before publishing major implementations in these areas:

- eval-driven generator optimization
- customer-derived repair or migration heuristics
- proprietary evaluation artifacts
- cross-customer intelligence
- learned routing or optimization
- provider-specific production execution
- payment or commercial economics
- marketplace ranking
- production operational intelligence
- production credential topology
- customer/operator feedback signals when used for hosted learning, ranking, or optimization

Publication review does not mean the code must be private. It means the repository should record why MIT publication creates enough adoption, trust, portability, interoperability, contribution, or ecosystem value to justify the exposure.

Generic mechanism/code may stay open even when proprietary data used to improve
it stays out of this repository. For example, local source discovery and
deterministic App Intelligence are framework capabilities; accumulated repair
data, eval results, learned rankings, and cross-app outcome correlations are
publication-review items.

## Public Interface, Reviewed Implementation

Prefer public contracts with reviewed implementations when a capability needs ecosystem trust but the operating implementation may contain commercial or security-sensitive detail.

Examples:

- public provider contract, reviewed production executor
- public payment capability schema, reviewed money-movement implementation
- public telemetry event schema, reviewed cross-customer analysis
- public validation facade, reviewed proprietary evaluation corpus
- public build-context pack contract, reviewed customer-derived optimization

## One-Way Door Standard

Create an ADR when a change would make a meaningful public commitment or expose hard-to-retract knowledge:

- new public schema family or protocol
- new workflow or prompt family with material generation strategy
- new provider mutation path
- new authority or permission bypass
- new managed-capability contract
- new dataset, benchmark, eval, or learned policy

The ADR should be short. It only needs to state the decision, reason, alternatives, consequences, reversibility, affected invariants, and whether the OSS/commercial boundary changes.
