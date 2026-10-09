# D30 authenticated action cohort evidence

`mozaiksai.core.metrics.d30_cohort.calculate_d30_action_cohort` supplies
provider-neutral, app-local D30 cohort math. It joins authoritative first
eligibility facts to successful authenticated end-user actions. It has no
network call, hosted policy, billing rule, or investor score.

## User value

App owners can distinguish a real day-30 return rate from page views, repeat
clicks, or a snapshot called “retention.” An explicit unavailable state keeps
early or incomplete data from appearing as a misleading zero or percentage.
The join stays in the customer app: a host can receive aggregate counts and
coverage evidence without receiving person or session IDs. Explicit deployment
environment scope keeps test activity out of live evidence. This supports
trustworthy progress reporting while limiting the personal data shared with a
hosting operator.

## Contract

For one UTC `cohort_date` and exact
`(app_id, environment, tenant_id, workspace_id)` scope:

- **Denominator:** distinct stable end-user IDs whose *first* eligibility
  timestamp is in `[cohort_date 00:00, next day 00:00)` UTC. The app that owns
  account or membership records decides and documents eligibility; it must
  exclude operators, test users, service identities, and other ineligible
  actors. `environment` is required and cannot be an empty or `default`
  placeholder; the app must stamp its actual deployment environment on every
  row and coverage assertion. A missing tenant or workspace ID means a
  genuinely app-wide scope, never a wildcard over separate scopes.
- **Numerator:** denominator IDs with at least one qualifying successful
  action during `[cohort_date + 30 days, cohort_date + 31 days)` UTC. The
  observation must have validated-token provenance and `actor_kind=end_user`.
  The caller supplies an explicit set of qualifying canonical action IDs.
  Repeated eligibility rows and repeated actions count once per ID.
- **Maturity and completeness:** the full D30 day must have elapsed. Both
  eligibility and action sources must attest authoritative, continuous,
  reconciled coverage across their respective windows. A late-arrival policy
  belongs to the source: its `complete_through` watermark must advance only
  after late events are accounted for. Coverage must name the same exact app,
  environment, tenant, and workspace scope as the input rows; its watermark
  cannot be in the future relative to `as_of`. Gaps, absent authority,
  unmatured days, conflicting first eligibility, malformed identity or
  timestamp, and mixed scopes yield `status=unavailable` with no counts.
- **Zero:** with a nonempty denominator and complete observation, zero
  qualifying actions yields an available `0%`. An empty cohort is unavailable.

`EligibleEndUser` and `ActionObservation` are input facts supplied by the
consuming app. `SourceCoverage.authoritative=True` is an attestation by that
app's trusted source adapter, not a claim that this pure calculator can prove.
Never set it from a browser request, a configuration default, or the existence
of some events in a collection. The app must also prove that its eligibility
source contains the first eligible instant and a stable end-user identity,
and that its action source captures all qualifying paths without loss.

The `D30CohortResult` contains scope, windows, availability, counts, and rate;
it contains no user IDs. A hosted reporter may transmit only this aggregate
and its verified source coverage, with an app-owned authorization and delivery
contract. The calculator intentionally does not add a host-reporting route.

## Current delivery limit

`AppMetrics` stores app-scoped events, and automatic `app.action_invoked`
instrumentation runs after successful HTTP module dispatch. That write is
fire and forget, can be disabled, and suppresses write failures. Its records
do not prove validated-token provenance, end-user status, complete capture,
or first eligibility. The generated cloud usage reporter sends daily usage
rollups only. Neither source can currently attest the required D30 inputs,
so installing this calculator alone cannot produce a production D30 rate.

An app integration must first provide authoritative scoped eligibility and
reconciled action sources, source-derived coverage watermarks, stable identity
and end-user classification, an action allowlist, and an authorized aggregate
delivery path. Existing owner analytics `retention` is a period ratio from
churn snapshots; it is not this cohort metric.
