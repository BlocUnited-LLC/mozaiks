# Framework And Operator Intelligence Boundary

Mozaiks separates generic intelligence about one application from intelligence
derived by operating or evaluating many applications.

## Framework / App Intelligence

Framework intelligence is generic understanding of the current application. It
should work locally and independently through OSS.

Examples:

- source indexing and source retrieval
- framework detection
- `SourceContextBundle`, `AppContextGraph`, `AppIntelligenceSnapshot`, and
  `AppContextVersion`
- baseline `AppGenerator`
- baseline `AgentGenerator`
- baseline `ExistingAppDiscovery`
- baseline refinement and routing context
- build-context packs and declarative prompt context
- deterministic validation and quality gates
- structured-output validation
- generic telemetry/event schemas that do not require customer identity

This intelligence may be sophisticated. It remains framework intelligence when
it is derived from the application currently being analyzed and does not depend
on BlocUnited customer history or hosted operating data.

## Operator / Learned Intelligence

Operator intelligence is knowledge accumulated by an operator across
applications, users, evals, hosted production outcomes, or commercial
relationships.

Examples:

- correction corpora
- eval corpora and eval results
- learned rankings
- production outcome correlations
- learned migration heuristics
- eval-informed model routing
- customer-derived context strategy
- cross-app failure or quality patterns
- marketplace, payment, hosting, or commercial-success intelligence

Operator intelligence is not automatically part of OSS. Publishing learned
assets or many-app optimization logic requires publication review.

## Boundary Rule

Open the ability to understand and build one application. Deliberately review
what BlocUnited learns from understanding or operating many applications.

This is a framework/operator ownership boundary, not a rule that useful code
must be private. Generic algorithms, schemas, validators, and baseline prompts
can remain open even when proprietary datasets or eval evidence used to improve
future versions remain outside this repository.

## Current OSS Position

The current OSS App Intelligence plane is designed to be rebuildable from local
source, app workspace files, generated artifacts, validation evidence, and
promotion state. Graph and search indexes are mirrors, not authority.

Existing-app discovery belongs in OSS because brownfield understanding is part
of the one-application framework baseline. Future learned migration ranking,
customer-derived repair heuristics, cross-project retrieval, and eval-informed
planning are publication-review items.

## Telemetry

Framework telemetry may describe structural build, validation, dispatch, and
reaction facts. It should not require customer satisfaction, customer identity,
or operator-specific commercial state.

Self-hosted operators may choose to emit telemetry to their own backends. The
OSS contract should remain understandable without BlocUnited-hosted Build
Intelligence.
