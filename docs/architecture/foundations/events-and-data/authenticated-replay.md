# Authenticated journal replay

`create_replay_router` in `mozaiksai.core.transport` delivers a host-selected
durable journal over a separate WebSocket. The runtime owns authentication,
cursor validation, bounded paging, heartbeat and connection cleanup. The source
owns storage, current product authorization, retention and safe projections.
This does not replace workflow chat transport, UI artifact rendering or HTTP
actions. It is a delivery-only endpoint, with no client writes or multiplexing.

## Composition

```python
from mozaiksai.core.runtime.composition import PlatformExtensionBundle
from mozaiksai.core.transport import create_replay_router


async def on_startup(app):
    # A host-selected source; clients cannot name adapters, actions or collections.
    source = make_application_journal_source(app)
    app.include_router(
        create_replay_router(source, allowed_origins=("https://app.example.com",)),
        prefix="/ws/journal",
    )


def get_bundle():
    return PlatformExtensionBundle(on_startup=on_startup)
```

The existing platform startup hook runs after module loading. No new module
`runtime_extensions.yaml` transport engine or loader taxonomy is required.
Startup-hook failures are logged by the existing host: an unavailable source
must leave delivery unavailable, never grant access. The router creates no
global background worker and needs no separate shutdown hook.

The route is `/{app_id}/{stream_id}` relative to its mounted prefix. Browsers
use `openAuthenticatedWebSocket` from `@mozaiks/chat-ui/auth`. Bearer credentials
travel in the existing subprotocol header. Only one optional `cursor` query is
allowed; URL credentials are refused even when legacy query-token development
mode is enabled. Browser origins require an exact configured match. Clients
without an Origin still require authentication. Development/anonymous identities,
another app's bound identity and workflow-chat-bound tokens are refused. The
validated principal must carry a finite, future numeric `exp` claim.

An app claim is optional, matching canonical `WebSocketUser.validate_app_id`:
human access tokens without an app binding are allowed through authentication.
They gain no app access from the route or cursor. The source must authorize the
current principal for the exact requested app and stream before any read; denial
closes the connection without reading. A present app binding must match the route.

## Source contract

Public types are `ReplaySource`, `ReplayGrant`, `ReplayPage`, `ReplayEvent`,
`ReplayLimits` and `create_replay_router`.

| Method or result | Required meaning |
| --- | --- |
| `authorize(principal, *, app_id, stream_id)` | Consult current permissions and token scope. Return `ReplayGrant(app_id, stream_id, epoch, authorization_version)` or raise `PermissionError`. |
| `read_page(principal, *, grant, after, limit)` | Return one consistent `ReplayPage(epoch, floor, head, events)`. `after=None` requests metadata only, with no events. |
| `floor` | Earliest resumable sequence; older cursors require a fresh snapshot. |
| `head` | Highest committed sequence in this epoch. Every sequence in `(floor, head]` exists. |
| `events` | Tuple of `ReplayEvent(sequence, event_id, data)`; exactly the next `min(limit, head-after)` consecutive events for a valid cursor. Return no events for an older/future cursor. |
| `authorization_version` | Opaque current audience/consent generation, separate from the journal epoch. It must change when a previously authorized projection becomes forbidden, including applicable account-erasure fences. |

Identifiers and versions are bounded ASCII identifiers (letters, digits,
underscore, period, colon or hyphen, starting with a letter/digit; at most 128
characters). Sequences are integers from zero through `2**53-1`. Data is a JSON
object with no nonfinite numbers. The transport validates result types, scope,
order, continuity, frame size and monotonic head; invalid source output fails
closed. IDs must be stable and unique in the source across retries/pages.

The source must provide transactionally committed order, not timestamps or
process-local event counters. One approach is a per-stream counter and journal
reference written atomically with the mutation through the public AppData
transaction callback. Persist stable operation IDs and reconcile uncertain
commits. The transport itself supplies no outbox, storage or delivery guarantee
independent of the source.

Each source call receives the authenticated principal and each read receives the
exact grant. Reauthorize after the read, immediately before sending: an epoch
change produces resync; any other grant change discards the page and closes the
connection. Authorization is checked while idle too; an authorization-version change between pages also closes
the connection, even when the new grant still permits reads. Clients clear the
old projection and establish a new authorized snapshot after such a change.
This is authorization at
the send boundary, not a claim that later revocation can retract already sent
bytes. Sources must return current redacted/tombstone projections, never revive
historical body/identity caches. App-owned adapters use existing public module
dispatch authority and owning module policy/repositories; this API grants no
permission-free context or raw persistence access.

## Wire and recovery

Every text frame has `schema_version: "mozaiks.replay.v1"`:

- `ready`: an initial committed-head `cursor`. No historical payload is sent.
- `batch`: `after`, resulting `cursor`, and an ordered `events` array containing
  `sequence`, `event_id` and `data`. Apply the entire batch before saving its cursor.
- `heartbeat`: the current cursor, without changing the applied position.
- `resync_required`: reason `retention_gap`, `cursor_ahead` or `epoch_changed`,
  followed by a normal close. Never silently skip to a new head.

A connection with no cursor obtains only current metadata and starts at that
head. The client must then establish its authorized HTTP snapshot while retaining
subsequent batches, merge them using stable IDs/revisions, and bound that buffering.
Buffer overflow requires resync; silently discarding buffered batches is invalid.
This snapshot/live handoff must be proven by the paired application; a `ready`
frame alone is not a snapshot. Alternatively the app can supply a snapshot and
matching cursor through its own consistency boundary. Opaque cursors encode the
version, exact app/stream, epoch and sequence; they are untrusted positions, not
capabilities or authorization tokens.

Reconnect with the last fully applied batch cursor. Delivery is at least once:
the server may have sent a batch the client did not apply. The source and client
must support idempotent replay. Retention or epoch resync discards the old
projection and repeats the app's snapshot handoff after reauthorization.

The default source poll interval is one second, heartbeat interval fifteen
seconds, page limit fifty, frame limit 128 KiB and source/send timeout five
seconds. This is **server-side journal polling**, not instant event-bus delivery.
It avoids a lossy backfill/live queue handoff: every next page reads the journal.
Slow sends terminate with 1011 and retain recovery through the client's cursor.
Expiry and access denial close with 1008. Unexpected source failures return only
a fixed 1011 reason; this transport logs no payloads or source exception text.
Clients send no application messages. Configure the ASGI server's inbound frame
limit and connection/rate limits at the host boundary.

All source operations must cooperate with cancellation. Disconnect, expiry,
timeouts and handler cancellation cancel pending source work/sends and dispose
the connection tasks. A source that ignores cancellation violates this contract.
Expiry is checked before invoking each authorization, read or send, including
after frame serialization. Already-expired operations are never constructed;
pending operations remain subject to the expiry deadline and cancellation.
There is no independent session manager, durable acknowledgement or queue.

## Validation and limits

`python -m pytest --no-cov --reruns 0 tests/test_replay_transport.py` exercises a
synthetic issuer/journal over owned ephemeral loopback TCP listeners. It covers
auth negatives, pagination/reconnect with a new router, initial metadata bounds,
retention/epoch resync, policy changes during reads, idle expiry/revocation,
malformed source output and cancellation during reads and blocked ASGI sends.
The blocked-send tests retain real socket auth/lifecycle but inject backpressure
deterministically at the ASGI send boundary.

This protocol proof is not a real journal integration, public deployment or
whole-host acceptance. Keep existing client delivery until a paired source proves
transactional ordering, uncertain-commit reconciliation, retention, erasure,
snapshot handoff and replay across actual process restarts. No generator,
Factory workflow, module manifest or existing workflow-artifact contract changes.
