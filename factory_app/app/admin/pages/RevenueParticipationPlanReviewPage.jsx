/**
 * RevenueParticipationPlanReviewPage — Plan proposal review and approval.
 *
 * Shows a RevenueParticipationDesignerWorkflow artifact in full detail and
 * allows the operator to confirm creation of a draft revenue plan, then
 * optionally activate it. All actions require explicit human confirmation
 * checkboxes. No money moves automatically.
 *
 * Route: /apps/:appId/revenue-participation/plan-proposals/:proposalId
 */

import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Button,
  InlineEmptyState,
  LoadingState,
  ErrorState,
  Panel,
  StatusPill,
  SummaryStrip,
} from '@mozaiks/chat-ui/ui'

import { callModuleAction } from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanizeLabel(value) {
  return String(value || 'unknown').replace(/_/g, ' ')
}

function BulletList({ title, items }) {
  if (!items?.length) return null
  return (
    <div className="mt-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((item, idx) => (
          <li key={idx} className="flex gap-2 text-sm">
            <span className="shrink-0 select-none text-muted-foreground">•</span>
            <span className="text-foreground">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RevenueParticipationPlanReviewPage() {
  const { appId, proposalId } = useParams()

  const [proposal, setProposal] = useState(null)
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)

  // Create plan state
  const [confirmCreate, setConfirmCreate] = useState(false)
  const [creatingPlan, setCreatingPlan] = useState(false)
  const [createError, setCreateError] = useState(null)
  const [createdPlan, setCreatedPlan] = useState(null)

  // Activate plan state
  const [confirmActivate, setConfirmActivate] = useState(false)
  const [activatingPlan, setActivatingPlan] = useState(false)
  const [activateError, setActivateError] = useState(null)
  const [activatedPlan, setActivatedPlan] = useState(null)

  useEffect(() => {
    if (!proposalId) {
      setLoading(false)
      return
    }
    callModuleAction('community_revenue_participation', 'get_revenue_plan_proposal', {
      proposal_id: proposalId,
    })
      .then((result) => {
        if (result?.found && result.proposal) {
          setProposal(result.proposal)
        } else {
          setPageError(result?.error || 'Proposal not found.')
        }
      })
      .catch(() => setPageError('Failed to load proposal.'))
      .finally(() => setLoading(false))
  }, [proposalId])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading plan proposal..." />
  if (pageError) return <ErrorState title="Proposal unavailable" message={pageError} />
  if (!proposal) return <InlineEmptyState title="Proposal not found" />

  const proposedPlan = proposal.proposed_plan || {}
  const nextStep = proposal.next_step || {}

  const summaryItems = [
    {
      id: 'human-review',
      label: 'Human Review',
      value: proposal.human_review_required ? 'Required' : 'Not required',
      detail: 'Operator must review before any plan action',
    },
    {
      id: 'distribution-basis',
      label: 'Distribution Basis',
      value: humanizeLabel(proposedPlan.distribution_basis || '—'),
      detail: proposedPlan.rationale ? proposedPlan.rationale.slice(0, 80) : '',
    },
    {
      id: 'next-action',
      label: 'Next Step',
      value: nextStep.target_action || '—',
      detail: 'Operator must call this action manually after review',
    },
    {
      id: 'community',
      label: 'Community',
      value: proposal.community_name || proposal.community_id || '—',
      detail: `App: ${proposal.app_id || appId || '—'}`,
    },
  ]

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleCreatePlan() {
    setCreatingPlan(true)
    setCreateError(null)
    try {
      const result = await callModuleAction(
        'community_revenue_participation',
        'create_revenue_plan_from_proposal',
        { proposal_id: proposalId, confirm_create: true },
      )
      if (result?.success) {
        setCreatedPlan(result)
      } else {
        setCreateError(result?.error || 'Failed to create plan.')
      }
    } catch {
      setCreateError('Request failed.')
    } finally {
      setCreatingPlan(false)
    }
  }

  async function handleActivatePlan() {
    setActivatingPlan(true)
    setActivateError(null)
    try {
      const result = await callModuleAction(
        'community_revenue_participation',
        'activate_revenue_plan',
        { plan_id: createdPlan.plan_id },
      )
      if (result?.success) {
        setActivatedPlan(result)
      } else {
        setActivateError(result?.error || 'Failed to activate plan.')
      }
    } catch {
      setActivateError('Request failed.')
    } finally {
      setActivatingPlan(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        {/* ---- Page header ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                App Studio · Revenue Participation
              </div>
              <h1 className="mt-1 text-2xl font-semibold text-foreground">
                Plan Proposal Review
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {proposal.community_name || proposal.community_id || 'Community'}
                {proposal.app_id ? ` · ${proposal.app_id}` : ''}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatusPill tone="warning">Human review required</StatusPill>
                <StatusPill tone="default">
                  {humanizeLabel(proposedPlan.status || 'proposed')}
                </StatusPill>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link to={`/apps/${appId}/revenue-participation`}>
                  Back to revenue participation
                </Link>
              </Button>
            </div>
          </div>
        </div>

        {/* ---- HITL boundary notice ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
          <div
            role="alert"
            data-testid="hitl-boundary-notice"
            className="rounded-xl border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-warning"
          >
            <p className="font-semibold">
              Operator review required — no actions are taken automatically.
            </p>
            <p className="mt-1 text-xs text-warning/80">
              This page does not call <code>create_revenue_plan</code>,{' '}
              <code>activate_revenue_plan</code>, <code>execute_settlement</code>, or any
              wallet/payout action. The next step action (
              <code>{nextStep.target_action || '—'}</code>) must be called manually by the
              operator after review.
            </p>
          </div>
        </div>

        {/* ---- Summary strip ---- */}
        <Panel
          title="Proposal summary"
          subtitle="Generated by RevenueParticipationDesignerWorkflow."
        >
          <SummaryStrip items={summaryItems} />
        </Panel>

        {/* ---- Plan detail + next step ---- */}
        <div className="grid gap-6 xl:grid-cols-2">
          <Panel
            title="Proposed plan"
            subtitle="Distribution configuration proposed for this community."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Distribution basis
                </div>
                <div className="mt-2 text-lg font-semibold text-foreground">
                  {humanizeLabel(proposedPlan.distribution_basis || '—')}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Eligible roles
                </div>
                <div className="mt-2 text-sm font-semibold text-foreground">
                  {proposedPlan.eligible_roles?.length
                    ? proposedPlan.eligible_roles.join(', ')
                    : 'All active members'}
                </div>
              </div>
            </div>

            {proposedPlan.distribution_basis === 'role_weighted' && proposedPlan.role_weights && (
              <div className="mt-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Role weights
                </div>
                <div className="rounded-2xl border border-border bg-muted/20 px-4 py-3">
                  {Object.entries(proposedPlan.role_weights).map(([role, weight]) => (
                    <div
                      key={role}
                      className="flex items-center justify-between py-1 text-sm"
                    >
                      <span className="font-medium text-foreground">{role}</span>
                      <span className="font-mono text-foreground">{weight}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {proposedPlan.rationale && (
              <div className="mt-4 rounded-2xl border border-border bg-muted/20 px-4 py-3 text-sm text-foreground">
                {proposedPlan.rationale}
              </div>
            )}
          </Panel>

          {/* Next step / operator action */}
          <Panel
            title="Next step"
            subtitle="Operator action required after review and approval."
          >
            <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Target action
              </div>
              <div
                className="mt-2 font-mono text-sm text-foreground"
                data-testid="next-step-target-action"
              >
                {nextStep.target_action || '—'}
              </div>
              {nextStep.notes && (
                <div className="mt-3 text-sm text-muted-foreground">{nextStep.notes}</div>
              )}
              <div className="mt-3">
                <StatusPill tone="warning">Requires human approval</StatusPill>
              </div>
            </div>

            {proposal.approval_instructions && (
              <div className="mt-4 rounded-2xl border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                {proposal.approval_instructions}
              </div>
            )}

            {/* Create plan section / success state */}
            {createdPlan ? (
              <div className="mt-4 space-y-4">
                <div
                  className="rounded-xl border border-border bg-muted/10 px-4 py-3 text-sm"
                  data-testid="create-plan-success"
                >
                  <p className="font-semibold text-foreground">Draft plan created.</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Plan ID: <span className="font-mono">{createdPlan.plan_id}</span> · Status:{' '}
                    {activatedPlan ? 'active' : createdPlan.status || 'draft'}
                  </p>
                  {!activatedPlan && (
                    <p
                      className="mt-1 text-xs text-warning"
                      data-testid="plan-not-active-warning"
                    >
                      This plan is not active yet.
                    </p>
                  )}
                </div>

                {activatedPlan ? (
                  <div
                    className="rounded-xl border border-border bg-muted/10 px-4 py-3 text-sm"
                    data-testid="activate-plan-success"
                  >
                    <p className="font-semibold text-foreground">Revenue plan activated.</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Status: active
                      {activatedPlan.previous_archived_count > 0
                        ? ` · ${activatedPlan.previous_archived_count} prior plan(s) archived`
                        : ''}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Revenue participation is now configured for this community. Settlement
                      periods can be created when ready.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3" data-testid="activate-plan-section">
                    <div className="rounded-xl border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-warning">
                      <p className="font-semibold">Before activating:</p>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-warning/80">
                        <li>Activating this plan affects future settlement periods only.</li>
                        <li>Existing settlement periods and claims are not recalculated.</li>
                        <li>No payouts are executed.</li>
                        <li>
                          Any currently active plan for this community will be archived.
                        </li>
                      </ul>
                    </div>

                    <label className="flex cursor-pointer select-none items-start gap-3">
                      <input
                        type="checkbox"
                        checked={confirmActivate}
                        onChange={(e) => setConfirmActivate(e.target.checked)}
                        data-testid="confirm-activate-plan-checkbox"
                        className="mt-0.5 shrink-0 accent-primary"
                      />
                      <span className="text-sm text-foreground">
                        I understand and want to{' '}
                        <strong>activate this revenue plan</strong>.
                      </span>
                    </label>

                    {activateError && (
                      <p
                        className="text-sm text-destructive"
                        data-testid="activate-plan-error"
                      >
                        {activateError}
                      </p>
                    )}

                    <Button
                      disabled={!confirmActivate || activatingPlan}
                      data-testid="activate-revenue-plan-btn"
                      onClick={handleActivatePlan}
                    >
                      {activatingPlan ? 'Activating...' : 'Activate Revenue Plan'}
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                <div className="rounded-xl border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-warning">
                  <p className="font-semibold">Before confirming:</p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-warning/80">
                    <li>
                      This creates a <strong>draft plan only</strong> — revenue participation
                      is not yet active.
                    </li>
                    <li>Activation requires a separate operator step.</li>
                    <li>
                      No settlement period is created. No claims are computed. No money moves.
                    </li>
                  </ul>
                </div>

                <label className="flex cursor-pointer select-none items-start gap-3">
                  <input
                    type="checkbox"
                    checked={confirmCreate}
                    onChange={(e) => setConfirmCreate(e.target.checked)}
                    data-testid="confirm-create-plan-checkbox"
                    className="mt-0.5 shrink-0 accent-primary"
                  />
                  <span className="text-sm text-foreground">
                    I have reviewed this proposal and want to create a{' '}
                    <strong>draft revenue plan only</strong>.
                  </span>
                </label>

                {createError && (
                  <p className="text-sm text-destructive" data-testid="create-plan-error">
                    {createError}
                  </p>
                )}

                <Button
                  disabled={!confirmCreate || creatingPlan}
                  data-testid="create-revenue-plan-btn"
                  onClick={handleCreatePlan}
                >
                  {creatingPlan ? 'Creating...' : 'Create Draft Revenue Plan'}
                </Button>
              </div>
            )}
          </Panel>
        </div>

        {/* ---- Member composition ---- */}
        {proposal.member_composition_summary && (
          <Panel
            title="Member composition"
            subtitle="Community membership context used for this proposal."
          >
            <div className="rounded-2xl border border-border bg-muted/20 px-4 py-3 text-sm text-foreground">
              {proposal.member_composition_summary}
            </div>
          </Panel>
        )}

        {/* ---- Assumptions / risks / notes ---- */}
        <div className="grid gap-6 xl:grid-cols-2">
          {proposal.assumptions?.length > 0 && (
            <Panel
              title="Assumptions"
              subtitle="Key assumptions this proposal is based on."
            >
              <BulletList title="" items={proposal.assumptions} />
            </Panel>
          )}

          {proposal.risks?.length > 0 && (
            <Panel
              title="Risks"
              subtitle="Open questions and concerns for reviewer evaluation."
            >
              <ul className="flex flex-col gap-1.5">
                {proposal.risks.map((risk, idx) => (
                  <li key={idx} className="flex gap-2 text-sm">
                    <span className="shrink-0 select-none font-bold text-warning">!</span>
                    <span className="text-foreground">{risk}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>

        <Panel
          title="Operator notes"
          subtitle="Notes provided by the operator when starting the workflow."
        >
          {proposal.operator_notes ? (
            <div className="rounded-2xl border border-border bg-muted/20 px-4 py-3 text-sm text-foreground">
              {proposal.operator_notes}
            </div>
          ) : (
            <InlineEmptyState
              title="No operator notes"
              description="No notes were recorded for this proposal run."
            />
          )}
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
