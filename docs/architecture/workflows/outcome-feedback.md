# Workflow outcome feedback

Status: implementation in review.

Mozaiks owns the reusable interaction and validation contract for a person to
rate an outcome produced by a workflow. The app owns its feedback records and
any analysis of them. This extends the existing authenticated workflow UI tool
transport; it does not add a telemetry receiver or an agent evaluation engine.

The shipped `OutcomeFeedback` component collects an optional 1–5 rating,
helpfulness, and a reported outcome. A user can skip. Feedback follows delivery
of the result and never determines whether the user may receive it. Free text,
prompts, transcripts, and customer documents are excluded from this contract.

## Runtime contract

The public module `mozaiksai.core.workflow.outcome_feedback` exports:

- `WorkflowFeedbackResponse`: strict, extra fields forbidden. `status` is
  `submitted` or `skipped`; optional `rating` is an integer 1–5, `helpful` is a
  boolean, and `outcome` is `succeeded`, `partial`, `failed`, or `unknown`.
  Submission needs at least one answer; skipping cannot contain answers.
- `WorkflowFeedbackEvidence`: version `mozaiks.workflow_feedback.v1`, kind
  `human_feedback`, app/run/workflow/agent/outcome/user/event identifiers,
  `observed_at`, and the typed response. This is private, app-scoped data.
- `await collect_workflow_feedback(context_variables, *, tool_id, agent_name,
  outcome_id)`: uses an active workflow tool's runtime context. The declared
  producer agent and stable result identifier must come from workflow code or
  server-owned artifacts. Ratings and identities are never tool arguments.
- `await resolve_workflow_feedback(*, app_id, chat_id, user_id, ui_event_id,
  outcome_id)`: resolves a durable receipt in an exact authorized scope. Returns
  typed evidence or `None` for absent/mismatched scope; database failures
  propagate. Malformed stored evidence raises validation errors.

The UI sends answers through the existing HTTP/WebSocket UI response transport.
It cannot supply attribution. After the session-owner check, the transport saves
an immutable first response in
`ChatSessions.workflow_ui_state.feedback_receipts`, before releasing the waiter.
The receipt survives process restarts and is separate from replaceable display
metadata. Save failure leaves the interaction pending. Internal responses that
bypass the owner-checked entry point cannot create a human-feedback receipt.
An accepted retry retains the original response and observation timestamp.

The shipped browser component separately acknowledges an invitation after at
least half of it intersects the viewport in a visible document. The
`/api/workflow-feedback/rendered` endpoint accepts only the UI event ID. It
checks the authenticated session owner and the server's pending
`outcome_feedback` event, then stores one immutable
`mozaiks.workflow_feedback_render.v1` receipt in
`ChatSessions.workflow_ui_state.feedback_render_receipts`. Client fields cannot
choose app, chat, user, workflow, producer agent, or outcome attribution. A
retry preserves the first server observation time. Storage or authorization
failure leaves no render receipt and never answers the tool. The transport can
offer an invitation that is buffered, dropped, offscreen, or rendered in a
hidden tab; none of those offers becomes a render receipt.

The resolver is an internal runtime primitive, not an authorization boundary for
arbitrary browser fields. The consuming module must require workflow authority
and `workflow_tool` provenance, derive app/user from `ModuleContext` and chat
from `dispatch_provenance.workflow_run_id`, and verify the receipt's workflow.
Only `ui_event_id` and `outcome_id` cross the module action boundary. Never
accept an evidence blob or model-supplied ratings as proof of human origin.

## Generation and app storage

For an approved requirement to collect user feedback, select the
`outcome_feedback` capability pack. AgentGenerator knows the shipped
`outcome_feedback` primitive (`component: OutcomeFeedback`,
`realization: shipped_component`, inline) and validates that its tool accepts
only injected context and uses the canonical helper and module dispatcher:

```python
from mozaiksai.core.workflow.module_tools import dispatch_workflow_module_action
from mozaiksai.core.workflow.outcome_feedback import collect_workflow_feedback

async def rate_result(context_variables):
    evidence = await collect_workflow_feedback(
        context_variables, tool_id="rate_result", agent_name="AnswerAgent",
        outcome_id=context_variables.get("delivered_result_id"),
    )
    return await dispatch_workflow_module_action(
        "outcome_feedback", "record_workflow_feedback",
        {"ui_event_id": evidence.ui_event_id, "outcome_id": evidence.outcome_id},
    )
```

The example assumes `delivered_result_id` is protected server context and the
result was already delivered. A missing identifier is an error. Feedback may
remain unanswered; no response is invented or persisted in that case.

The pack supplies a private app module, module policy, repository, and additive
migration. DesignDocs must declare its `records` collection in
`data/contract.json`: `scope: app`, `entity: WorkflowFeedback`,
`tenancy: per_user`, `owner_field: user_id`, all evidence fields, and
`lifecycle.write_mode: workflow_write`. Its generated-module capability declares
`primary_entities: [WorkflowFeedback]`. The canonical compiler generates policy,
schemas, and the account export/deletion handler from this declaration. The
production assembler expands the selected pack before canonical compilation;
template paths stay outside model-owned tasks while receiving compiled methods.
The bundle scanner rejects missing ownership, incomplete evidence fields, and any
general create/update/delete action for feedback. Application
records use `ctx.persistence`; app/user isolation and unique indexes preserve
one response per app/user/chat/outcome and per event. A later invitation for the
same outcome cannot change that counted response. Session receipt retention
follows session lifecycle; copied app records follow the app's account policy.
The persistence API's `is_unique_constraint_violation(error)` identifies typed
storage conflicts without generated repositories importing a database driver.
The pack recovers only after finding its expected scoped row; other failures
propagate. Existing persistence adapter exception behavior is unchanged.

An app can use another explicitly declared module sink satisfying this contract.
Generated apps do not inherit a host operator's private analytics receiver.

## Interpretation and extension boundary

Human feedback describes the respondent's experience. It does not prove task
correctness, runtime reliability, commercial success, or future performance.
Report rating count, helpfulness count, and outcome count separately. Report
eligible outcomes and invitations alongside answers when those cohorts are
measured, and treat skipped and
unanswered invitations as missing feedback. Simulated personas, model judges,
test fixtures, and operator reviews must never enter the human-feedback series.

These are different facts: an eligible outcome can cause a server offer; a
visible browser can acknowledge rendering; a user may submit, skip, or leave
the invitation unanswered. A render receipt is an authenticated client report
of visibility, not proof that a person read the prompt. Old clients, offline
acknowledgements, and process loss can undercount rendered prompts; a response
may therefore exist without a render receipt. The framework does not yet
persist a complete eligible-outcome or offered-invitation series, nor copy
unanswered invitations into app-owned records. Do not publish an invitation
rate, response rate, or investor-facing satisfaction measure from these receipts
alone. A consuming app must instrument the full eligible/offered/rendered/
responded/skipped cohort, exclude local development and synthetic traffic, and
verify missing-ack coverage before calculating a rate.

The generic framework telemetry emitter continues to exclude satisfaction and
production outcome data. AG2 owns execution and semantic evaluation primitives;
applications can independently run an evaluation workflow without confusing its
results with authenticated human responses.

There is no simulation runner or evidence aggregation engine in this change.
Persona simulations and model evaluations need a separately typed app-owned
source, model/version, scenario, sample size, and assumptions. They cannot use
the human-feedback receipt contract. Runtime token costs remain available from
`get_runtime_usage_ledger().query_usage(app_id=..., limit=...)` as a bounded
recent-event inference estimate, with pricing coverage. That API does not
provide complete delivery cost, human-review cost, or generated-target cost
attribution.

## Verification

Focused tests cover strict answers, forged attribution, owner checks,
persistence failure, replay, scoped visible-render acknowledgements, cross-manager receipt resolution, generated tool
validation, and app storage. An opt-in real Mongo test verifies authenticated
HTTP submission through generated module persistence, concurrent deduplication,
and foreign-owner rejection. The generated fixture uses production assembly and
the runtime ModuleLoader verifies actions and account handler registration.
A Playwright test exercises rating/helpful/outcome,
authenticated requests, rejection/retry, double clicks, and skip at mobile width.
These tests are deterministic acceptance; they are not a paid live Factory
generation or a production user-feedback sample.
