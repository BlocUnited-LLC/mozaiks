# Evidence Core

Evidence Core is a host-neutral contract for observations about an app, workflow,
agent, deployment, or another explicitly named subject. It represents the
observation, its source, its quality, and the period it describes. The source
system remains authoritative for its original record.

`mozaiksai.core.evidence` provides strict records and a source-provider
protocol. It does not persist evidence, interpret business performance, grant
access or economic rights, or trigger money movement. Operators may keep
independent evidence repositories and immutable snapshots in their own apps.

An evidence value can be a scalar or a bounded set of named scalar fields.
`None` means unavailable and must carry `verification=unavailable` and
`completeness=unavailable`. A zero is a measured or reported value, never a
fallback for missing data. Verification and completeness are independent:
for example, a measured subset can be partial, and a complete report remains
reported. Synthetic records require both the synthetic source and verification
labels.

`EvidenceSource` retains the producing system and exact source record IDs (up
to 8,192 per record). Larger sets may use an immutable source-record-set
reference and digest. The producer must persist the exact manifest: a digest
alone cannot enumerate inputs for an audit. Derived evidence must identify its
inputs. A stable canonical serialization and content digest let downstream
repositories bind an exact record version. Period boundary and denominator
semantics belong in the versioned methodology.

## AppMetrics projection

AppMetrics remains the app-scoped source for its numeric events and snapshots.
`project_app_metric_snapshot` converts only an explicitly identified numeric
snapshot. Its caller supplies evidence type, verification, completeness, and
methodology version. The projection retains the original AppMetrics ID, app
scope, correlation, period, and timestamps. It does not decide whether an MRR
snapshot is collected revenue or resolve a runtime app ID into a hosted
commercial app ID. An operator must perform that mapping through its own
trusted identity resolver.

## Evidence event

`EvidenceRecorded` is the version-1 payload contract for
`domain.evidence.recorded`. It carries the evidence ID, subject, source,
verification, completeness, methodology version, observation time, and content
digest. A publishing app module must declare this event in its own
`contracts/events.yaml` and emit it through `ctx.emit` after its evidence write.
The module event emitter supplies the standard ID, scope, correlation, actor,
and authority envelope and validates the declared payload schema. The event
announces an observation; it does not authorize a financial or operational
effect.

The model's `evidence_recorded_payload_schema()` is the canonical JSON Schema
for a module declaration. The publisher should keep its declared event schema
aligned with this model and test the emitted payload through module dispatch.
