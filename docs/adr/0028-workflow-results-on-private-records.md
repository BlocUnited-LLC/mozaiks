# ADR 0028. Saving a workflow result on its triggering private record

Proposed for the OSS runtime and reference Factory, 2026-11-25.

## Decision

An app can opt a module's create event into one automatic result write using
`target.result_write` on a workflow capability in `mozaiks.reactions.v1`.
The declaration identifies the source action, private collection, record key,
internal receiving action and writable fields. Factory presents those choices
in workflow plan review and compiles the approved binding into the app.

## Reason and alternatives

A signed-in user may ask an assistant to suggest a title after creating a note.
The assistant may finish after the request or browser session ends. Forwarding
the user's token would delegate all of its permissions. Accepting a record ID
from a model or chat variable would let the model select the write destination.
Requiring the user to return and confirm each result would preclude automatic
background completion.

## Consequences

The platform binds the workflow session to a single acknowledged insert by the
authenticated owner and pins the producing agent from the installed workflow.
The grant stays in server-owned session storage. Only the registered automatic
result operation on that agent receives its validated AG2 output for writing.
The internal receiver may approve
only that output. The host verifies the live invocation and current reaction,
then stores the result and its replay receipt in one conditional update. An
identical retry returns the original outcome. A changed retry is refused.

This additive v1 contract supports module-owned, per-user records. Existing
subscriptions keep their delivery behavior. Removing the reaction revokes pending
writes on the next registration. Previously committed fields remain app data.
Changing the public declaration later would require coordinating generated
apps and runtimes. Hosts can stop granting new writes by removing the declaration.

## Affected invariants and OSS boundary

Framework invariants 1, 3, 4 and 6 apply. The provider-neutral contract,
review, and ownership enforcement are public infrastructure for one app.
Keep private provider credentials, raw customer data and host-specific
authorization policy outside generated artifacts. [Architectural invariants](../../ARCHITECTURAL_INVARIANTS.md)

## Verification

Exercise the real event, authenticated insert, automatic tool and MongoDB
update. Check separate owners with matching public record IDs, modified
outputs, duplicate delivery, normal edit events, module re-registration,
expired tool invocations and session reopening. Verify Factory preserves
the pinned write plan across later app-only refinements.
