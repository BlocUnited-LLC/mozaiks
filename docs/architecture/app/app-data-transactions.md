# AppData transactions

App-owned alias repositories can atomically persist related records with
`run_app_data_transaction` from `mozaiksai.core.runtime.persistence`.
The runtime keeps the Mongo client and session private. The callback receives
a fresh `AppData` view bound to the transaction; the incoming context and its
cached view remain unchanged.

```python
# A module's backend/repo.py; IDs are allocated before invoking this method.
from mozaiksai.core.runtime.persistence import run_app_data_transaction


async def save_order_and_intent(ctx, *, order, intent):
    async def persist(data):
        await data.collection("orders.records").insert_one(order)
        await data.collection("orders.delivery_intents").insert_one(intent)
        return order["order_id"]

    return await run_app_data_transaction(ctx, persist)
```

The aliases must already exist in the app data contract. The runtime uses the
injected `MongoPersistenceContext` and its existing collection restrictions;
transactions do not make owned or platform-reserved collections available as
aliases. Operations use the canonical guarded alias argument and aggregation
checks. Alias repositories still supply their explicit app/domain filters and
document identity; a transaction grants no business authorization.

Use only the callback's view for participating operations. Existing handles on
`ctx.app_data` or `ctx.persistence` do not join automatically. This API supports
AppData aliases, not the generated-module `ctx.persistence.collection` surface.
Do not pass a session, start a nested transaction, spawn concurrent database
tasks, or retain a collection/cursor beyond its callback attempt. Those uses
are rejected. Use the existing repository/service boundary: persistence in
the repo, policy and event orchestration in the service.

MongoDB must support transactions: use a replica set or sharded cluster. The
helper requests snapshot reads, majority writes and primary read preference.
It never falls back to independent writes. Unsupported topology and database
errors propagate. Driver transaction retry behavior is delegated to Motor's
`with_transaction`, with a ten-second per-commit limit; the helper does not add
a second retry loop. An uncertain commit acknowledgement can propagate
immediately without a commit retry, including with Motor 3.7.1. Do not infer
that the driver's documented retry window guarantees a commit retry.

A callback may run more than once. Allocate stable IDs beforehand, keep all
external side effects outside it, and emit notifications only after successful
return. Persisting an intent alongside a record closes the producer's commit/
emit gap; delivery/reconciliation remains the owning module's responsibility.
An uncertain commit error does not prove rollback. Reconcile using the stable
operation ID before retrying the product action. This helper does not provide
an outbox, replay protocol or delivery guarantee by itself.

Run the focused protocol/guard tests normally:

```powershell
python -m pytest --no-cov tests/test_app_data_transaction.py
```

For a real proof, explicitly select an owned disposable local replica set with
`MOZAIKS_APP_TRANSACTION_TEST_URI`, then run
`tests/test_app_data_transaction_real_mongo.py`. Each test creates a UUID-named
database and drops only that database. It does not reconfigure or stop the
server. The suite covers commit, abort, driver-requested callback replay and
guard/handle-lifetime preservation.

See [persistence and artifact storage](../foundations/events-and-data/persistence-and-artifact-storage.md)
for ownership and [data contracts](data-contracts.md) for aliases.
