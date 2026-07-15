# Internal Messaging

Internal messaging is the app-owned conversation substrate for user-to-user,
operator-to-user, and support-desk threads. It is not an external integration.
External providers such as email, SMS, push, or chat tools are delivery
adapters that can react to message events later.

## Ownership

| Layer | Owns |
| --- | --- |
| `messages` module | Threads, messages, read state, conversation scope, recipient fan-out events. |
| `workspace_support` module | First-party Studio support tickets, ticket status, severity, page context, feedback. |
| `support` build-context pack | Generated app support request metadata and support inbox pages. |
| `social` build-context pack | Optional friends, follows, invitations, posts, comments, reactions, and activity feed records. |
| Platform notifications | In-app notification persistence and notification center visibility. |
| Integration modules | Optional external delivery through provider-specific adapters. |

This separation keeps support from becoming a second chat system. Support
records reference `message_thread_id`; the conversation itself lives in the
`messages` module.

## Active Studio Modules

```text
factory_app/app/modules/messages/
factory_app/app/modules/workspace_support/
```

`messages` exposes:

| Action | Purpose |
| --- | --- |
| `create_thread` | Create a `group`, `direct`, or `support` thread. |
| `list_threads` | List threads for the current participant. |
| `get_thread` | Fetch a thread and messages. |
| `send_message` | Persist a message and emit recipient notification facts. |
| `mark_thread_read` | Store read state for the current user. |

Message threads carry:

| Field | Meaning |
| --- | --- |
| `scope_type` | `app` for app-owned conversations, `workspace` for workspace/user social conversations. |
| `scope_id` | The app id or workspace id for the selected scope. |
| `subject_app_id` | The app the conversation is about. Support threads should set this even when Studio stores the record in a management persistence scope. |

`workspace_support` creates and links a support thread when a support request
is submitted. Operator replies call `MessageService.send_message()` with the
ticket owner as the recipient. The support module still emits
`domain.workspace_support.message_added` for audit and support-specific
automation, but user notification is owned by `domain.messages.message_sent`.

Support status is explicit:

- `open` means the ticket can still receive replies.
- `resolved` means the ticket is closed; the linked message thread is also
  marked resolved and the composer should be hidden until the ticket is
  reopened.
- "Needs reply" is derived UI state for open tickets where the latest support
  message came from the user or assistant transcript.
- "Responded" is derived UI state for open tickets where the latest support
  message came from an operator. Sending a reply does not resolve the ticket.

## Events And Notifications

`messages/contracts/events.yaml` declares:

- `domain.messages.thread_created`
- `domain.messages.message_sent`
- `domain.messages.thread_read`

`messages/contracts/notifications.yaml` uses `recipient_ids` from
`domain.messages.message_sent` to create in-app notifications. This is the
canonical path for "operator replied" notifications.

`workspace_support/contracts/notifications.yaml` owns support-queue alerts that
are not plain message-recipient delivery: new support requests, ticket-owner
replies, and negative feedback. These rules target principals with
`workspace_support.read`, so a user who is also an admin can see operator alerts
when their active principal has that scope. The profile support panel remains
user-scoped by default and lists only the current user's tickets; admin/support
queues must request `scope=app` or `scope=workspace` and carry support read or
manage permission.

Escalation UI should create a support request and navigate users to
`/me?tab=support-tickets`, optionally with `request_id` and `app_id` query
parameters. It should not send users to a generic `/support` route, because the
profile support tab is the durable user-facing transcript surface.

## Persistence

The `messages` module uses `ctx.persistence.collection()`:

| Entity | Collection |
| --- | --- |
| Threads | `messages`, `threads` |
| Messages | `messages`, `messages` |
| Read state | `messages`, `thread_reads` |

Support tickets stay under:

| Entity | Collection |
| --- | --- |
| Requests | `workspace_support`, `requests` |
| Feedback | `workspace_support`, `feedback` |

Generated social or support-heavy apps should reuse this contract instead of
creating module-local message arrays, support-only transcript collections, or
provider-specific chat stores.

## Modular Selection

- Select `messaging` when the app needs persisted conversations.
- Select `support` when the app needs a help desk, support inbox, or operator
  replies. `support` requires `messaging`.
- Select `social` when the app needs friends, follows, invitations, user posts,
  comments, reactions, or activity feeds.
- Keep governance features such as proposals, votes, quorum, moderation policy,
  and treasury/decision logic app-specific until there is a reusable governance
  pack. They can reference `messages` for deliberation threads, but they should
  not be generated as default social or support behavior.

Support is app-scoped. Social graph and DMs are not automatically app-scoped:
apps that need workspace-level social behavior should use `scope_type=workspace`
for message threads and keep social graph ownership in the `social` pack.

## Hosted Product Extensions

Hosted product workspaces such as `mozaiks-app` consume this substrate instead
of forking it. Hosted modules may add product policy, announcement surfaces,
operator routing, realtime fan-out, moderation, billing, marketplace, or
community-specific behavior, but the durable conversation state remains:

- `messages` owns threads, message bodies, participants, read state, and
  `domain.messages.*` lifecycle events.
- support modules own ticket metadata and reference `message_thread_id` instead
  of copying transcripts into a second store.
- social modules own friends, follows, invitations, posts, and feed records.
- notification modules consume declared events; they do not become message or
  support persistence.

Contacts are not a messaging primitive. A hosted or generated app should only
keep a `contacts` module if it has explicit private-address-book semantics that
are distinct from friends, follows, or workspace membership. A DM picker roster
should be backed by `social` relationships or a dedicated relationship provider,
not `messaging.contacts.*` capabilities.

Governance remains product-specific until there is a dedicated reusable pack.
Hosted community governance should use `hosted.*` events for proposal, voting,
quorum, treasury, moderation, and revenue-policy lifecycle. A future OSS
governance pack should be smaller and generic, such as proposals and decisions,
and may reference `messages` for deliberation threads.

## External Delivery

In-app notifications are the default OSS delivery path. Email, SMS, push, and
workspace chat providers should be added as integration-backed delivery
adapters that subscribe to `domain.messages.message_sent` and respect the same
recipient facts. Those adapters should not own thread or message persistence.

## Debugging Support Escalation

Use these log filters when verifying widget-to-profile support flows:

- Browser console: `[mozaiks-support]` for widget escalation and request creation.
- Browser console: `[mozaiks-profile]` for `/me` tab hydration and active tab sync.
- Browser console: `[mozaiks-support-panel]` for profile support ticket selection and replies.
- Backend logs: `workspace_support:` for request metadata, thread linkage, and support-message hydration.
- Backend logs: `messages:` for thread creation, message persistence, and recipient fan-out.

The profile URL may carry `app_id` as subject context, but profile panel
hydration must remain bound to the active runtime app. `/api/me/profile-panels`
passes that `app_id` to module actions as contextual input instead of using it
as the module execution persistence scope.
