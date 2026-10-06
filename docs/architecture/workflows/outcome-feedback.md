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

`mozaiksai.core.workflow.outcome_feedback.collect_workflow_feedback` binds the
response to server context (`app_id`, `chat_id`, `workflow_name`, `user_id`), a
declared agent and outcome, and the server-issued `ui_event_id`. HTTP and
WebSocket submissions use the existing session-owner check. The app persists
the returned evidence through a declared module action, deduplicating the
outcome and response event. A serialized evidence object alone is not proof of
authenticated origin: a public endpoint must not accept it as trusted input.

Human feedback describes the respondent's experience. It does not prove task
correctness, runtime reliability, commercial success, or future performance.
Report rating count, helpfulness count, and outcome count separately. Report
eligible outcomes and invitations alongside answers, and treat skipped and
unanswered invitations as missing feedback. Simulated personas, model judges,
test fixtures, and operator reviews must never enter the human-feedback series.

The generic framework telemetry emitter continues to exclude satisfaction and
production outcome data. AG2 owns execution and semantic evaluation primitives;
applications can independently run an evaluation workflow without confusing its
results with authenticated human responses.
