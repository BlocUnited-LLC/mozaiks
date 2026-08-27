# OSS Publication Policy

This repository is MIT-published framework infrastructure. Publication should be intentional because public MIT source is effectively a one-way door.

## Decision Rule

Open the ability to understand and build one application. Deliberately review what BlocUnited learns from understanding or operating many applications.

## Provenance Over Path

A file is not approved for publication merely because it lives in an OSS family.
Classify prompts, workflow YAML, scorer policy, repair heuristics, routing rules,
build-context packs, and agent instructions by provenance and information content.
Canonical contracts, generic engines, and a capable reference strategy belong in
OSS. Learned, customer-derived, fleet-derived, internally optimized, or otherwise
commercially differentiating intelligence is private by default unless an ADR
records sufficient ecosystem justification for publication.

Value, sophistication, or directory placement alone does not make generic
framework authority proprietary. Accepted ADR 0007's taxonomy, semantic-reference,
graph, binding, compilation, rendering, validation, revision, and refinement
contracts and generic deterministic engines remain public framework surfaces.

Proposed ADR 0005 would refine the reference-factory and build-intelligence
boundary if accepted.

## Fast Path

Changes are normally safe for the public repository when they improve:

- runtime and canonical app contracts
- validators and deterministic contract checks
- generated-app portability and self-hosting
- capable provider-neutral reference generation, discovery, and refinement
- generic App Intelligence that works from one app's source and public contracts
- public extension contracts
- provider-neutral deployment, secret, payment, data, and integration contracts
- local Studio, CLI, and developer tooling needed to operate OSS Mozaiks

These changes do not need an ADR unless they create a new public contract, expose a new authority surface, publish substantial generation or operations intelligence, or contain strategy whose provenance or competitive significance is uncertain.

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
