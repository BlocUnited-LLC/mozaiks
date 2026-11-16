# ADR 0025: Authenticated journal replay and app-data transactions

Date: 2026-11-14

Status: proposed; implementation in review.

## Decision

Publish `mozaiks.replay.v1`, a delivery-only WebSocket protocol:
`create_replay_router` with `ReplaySource`, `ReplayGrant`, `ReplayPage`,
`ReplayEvent` and `ReplayLimits` in `mozaiksai.core.transport`. The runtime owns
authentication, cursor validation, bounded paging, heartbeats and connection
cleanup. A host-selected source owns storage, current authorization, retention
and projections.

Publish `run_app_data_transaction` in `mozaiksai.core.runtime.persistence`. It
runs one callback in a MongoDB transaction over a fresh, guarded `AppData` view,
so an app can write a record and its journal reference atomically.

## Reason

Apps with live, multi-user surfaces need ordered, resumable delivery of durable
changes. Without a shared mechanism, each app reimplements socket bearer
handling, cursor validation, reconnect, backpressure and log redaction, and each
copy can get them wrong. The inputs are generic product requirements and
synthetic test fixtures. No customer data, learned policy or cross-app
intelligence is included.

## Alternatives considered

- Extending the workflow chat transport would put app-owned delivery inside the
  workflow session lifecycle.
- An in-process event bus with push delivery has a lossy handoff between backfill
  and live events and needs its own durability. Journal polling reads committed
  state for every page.
- Sockets authenticated by URL token are simpler for browsers but put
  credentials into access logs. The protocol accepts only the existing
  subprotocol bearer.
- Leaving transactions to each app would mean exposing the raw client and
  session, which bypasses the guarded `AppData` contract.

## Consequences

Every frame carries `schema_version: "mozaiks.replay.v1"`; the frame types are
`ready`, `batch`, `heartbeat` and `resync_required`. Cursors are opaque, bound to
the app, stream and epoch, and carry no authority. Delivery is at least once, so
sources and clients must be idempotent. A source must provide transactionally
committed order. It must also change `authorization_version` whenever a
projection that was authorized becomes forbidden.

The defaults are:
- one-second polling;
- a fifteen-second heartbeat;
- fifty events per page;
- 128 KiB frames;
- five-second source and send timeouts.

The router is inactive unless a host mounts it. No generator, Factory workflow,
module manifest or existing transport contract changes.

Transactions need a replica set or sharded cluster, and the helper never falls
back to independent writes. A callback may run more than once, and an uncertain
commit must be reconciled with a stable operation ID.

## Reversibility

Medium risk. The frame schema and cursor format are versioned public contracts,
and an incompatible change needs a new schema version. Both APIs are additive
and unused by default, so removing them before adoption is cheap. The
publication is MIT and contains generic delivery and transaction mechanics only.

## Affected invariants

- **1. Public Framework Contracts Stay Provider-Neutral.** Kept: the source
  interface names no storage product or provider.
- **2. Generated Artifacts Must Not Contain Raw Secrets.** Strengthened: URL
  credentials are refused, and the canonical log formatters redact socket
  credentials.
- **4. Public Schemas and Contracts Are Classified and Versioned.** The wire
  schema is `mozaiks.replay.v1`.
- **6. Authority Bypass Semantics Must Not Expand Casually.** Unchanged. The
  source authorizes the current principal for the exact app and stream before
  every read, and again before every send. The route, the cursor and the
  transaction helper grant nothing.

## OSS boundary

Keep in OSS: foundational delivery and persistence mechanics. Each consuming app
keeps its own domain models, membership, moderation and retention policy.

## Validation

- `tests/test_replay_transport.py` runs a synthetic issuer and journal over owned
  loopback sockets. It covers:
  - authentication refusals;
  - paging and reconnect;
  - resync;
  - policy changes during reads;
  - expiry;
  - malformed source output;
  - cancellation.
- `tests/test_app_data_transaction.py` covers the transaction guards.
- `tests/test_app_data_transaction_real_mongo.py` covers commit, abort and
  callback replay on a disposable local replica set.

See [authenticated journal replay](../architecture/foundations/events-and-data/authenticated-replay.md)
and [AppData transactions](../architecture/app/app-data-transactions.md).
