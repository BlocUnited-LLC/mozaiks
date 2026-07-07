/**
 * RevenueDistributionReviewPage — Distribution review artifact detail.
 *
 * Shows a RevenueDistributionReviewWorkflow artifact and allows the operator
 * to create a settlement period or approve it (with claims computation).
 * The distribution preview is advisory only — actual distribution is
 * recomputed live at the time of approval.
 *
 * Route: /apps/:appId/revenue-participation/distribution-reviews/:reviewId
 */

import { useState, useEffect, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Button,
  InlineEmptyState,
  LoadingState,
  ErrorState,
  Panel,
  ResourceList,
  StatusPill,
  SummaryStrip,
} from '@mozaiks/chat-ui/ui'

import { callModuleAction } from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanizeLabel(value) {
  return String(value).replace(/_/g, ' ')
}

function formatCurrency(amount, currency) {
  const numeric = typeof amount === 'number' ? amount : 0
  const code = currency.toUpperCase()
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: code }).format(
      numeric / 100,
    )
  } catch {
    return `${(numeric / 100).toFixed(2)} ${code}`
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RevenueDistributionReviewPage() {
  const { appId, reviewId } = useParams()

  const [artifact, setArtifact] = useState(null)
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)

  // Settlement action state
  const [confirmAction, setConfirmAction] = useState(false)
  const [performingAction, setPerformingAction] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [actionResult, setActionResult] = useState(null)

  useEffect(() => {
    if (!reviewId) {
      setLoading(false)
      return
    }
    callModuleAction('community_revenue_participation', 'get_distribution_review', {
      review_id: reviewId,
    })
      .then((result) => {
        if (result?.found && result.artifact) {
          setArtifact(result.artifact)
        } else {
          setPageError(result?.error || 'Distribution review not found.')
        }
      })
      .catch(() => setPageError('Failed to load distribution review.'))
      .finally(() => setLoading(false))
  }, [reviewId])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading distribution review..." />
  if (pageError) return <ErrorState title="Distribution review unavailable" message={pageError} />
  if (!artifact) return <InlineEmptyState title="Distribution review not found" />

  const nextStep = artifact.next_step || {}
  const activePlan = artifact.active_plan || {}
  const distributionPreview = artifact.distribution_preview || []
  const currency = artifact.currency || 'usd'
  const totalAmount = artifact.total_amount || 0

  const isCreateSettlement = (nextStep.target_action || '').includes('create_settlement_period')
  const isApproveSettlement = (nextStep.target_action || '').includes('approve_settlement_period')

  const summaryItems = [
    {
      id: 'human-review',
      label: 'Human Review',
      value: artifact.human_review_required ? 'Required' : 'Not required',
      detail: 'Operator must review before any settlement action',
    },
    {
      id: 'total-amount',
      label: 'Total Amount',
      value: formatCurrency(totalAmount, currency),
      detail: `${artifact.eligible_member_count || 0} eligible members`,
    },
    {
      id: 'period',
      label: 'Period',
      value:
        artifact.period_start
          ? `${artifact.period_start} → ${artifact.period_end || '?'}`
          : '—',
      detail: artifact.settlement_period_id
        ? `Period ID: ${artifact.settlement_period_id}`
        : 'No existing period — create first',
    },
    {
      id: 'next-action',
      label: 'Next Step',
      value: nextStep.target_action || '—',
      detail: 'Operator must call this action manually after review',
    },
  ]

  const previewColumns = useMemo(
    () => [
      {
        id: 'user_id',
        header: 'Member',
        render: (entry) => (
          <span className="font-mono text-sm text-foreground">{entry.user_id || '—'}</span>
        ),
      },
      {
        id: 'role',
        header: 'Role',
        width: '8rem',
        render: (entry) => (
          <span className="text-sm text-muted-foreground">{entry.role || '—'}</span>
        ),
      },
      {
        id: 'weight',
        header: 'Weight',
        width: '6rem',
        render: (entry) => (
          <span className="font-semibold text-foreground">
            {entry.distribution_weight ?? '—'}
          </span>
        ),
      },
      {
        id: 'amount',
        header: 'Computed amount',
        width: '10rem',
        render: (entry) => (
          <span className="font-semibold text-foreground">
            {formatCurrency(entry.computed_amount, currency)}
          </span>
        ),
      },
    ],
    [currency],
  )

  // ---------------------------------------------------------------------------
  // Action
  // ---------------------------------------------------------------------------

  async function handleSettlementAction() {
    setPerformingAction(true)
    setActionError(null)
    try {
      const result = isCreateSettlement
        ? await callModuleAction(
            'community_revenue_participation',
            'create_settlement_period_from_review',
            { review_id: reviewId, confirm_create: true },
          )
        : await callModuleAction(
            'community_revenue_participation',
            'approve_settlement_period_from_review',
            { review_id: reviewId, confirm_approve: true },
          )
      if (result?.success) {
        setActionResult(result)
      } else {
        setActionError(result?.error || 'Action failed.')
      }
    } catch {
      setActionError('Request failed.')
    } finally {
      setPerformingAction(false)
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
                Distribution Review
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {artifact.community_id || 'Community'}
                {artifact.app_id ? ` · ${artifact.app_id}` : ''}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatusPill tone="warning">Human review required</StatusPill>
                <StatusPill tone="muted">Advisory — recomputed on approval</StatusPill>
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
              Operator review required — no settlement actions are taken automatically.
            </p>
            <p className="mt-1 text-xs text-warning/80">
              This page does not call <code>create_settlement_period</code>,{' '}
              <code>approve_settlement_period</code>, <code>execute_settlement</code>, or any
              wallet/payout action. The distribution preview below is{' '}
              <strong>advisory only</strong> — actual distribution is recomputed live from
              current members and the active plan at the time of operator-confirmed approval.
            </p>
          </div>
        </div>

        {/* ---- Summary strip ---- */}
        <Panel
          title="Review summary"
          subtitle="Generated by RevenueDistributionReviewWorkflow."
        >
          <SummaryStrip items={summaryItems} />
        </Panel>

        {/* ---- Active plan + next step ---- */}
        <div className="grid gap-6 xl:grid-cols-2">
          <Panel
            title="Active plan"
            subtitle="Revenue plan used as the basis for this distribution preview."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Plan ID
                </div>
                <div className="mt-2 font-mono text-sm text-foreground">
                  {activePlan.plan_id || '—'}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Distribution basis
                </div>
                <div className="mt-2 text-lg font-semibold text-foreground">
                  {humanizeLabel(activePlan.distribution_basis || '—')}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Eligible roles
                </div>
                <div className="mt-2 text-sm font-semibold text-foreground">
                  {activePlan.eligible_roles?.length
                    ? activePlan.eligible_roles.join(', ')
                    : 'All active members'}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Total to distribute
                </div>
                <div className="mt-2 text-lg font-semibold text-foreground">
                  {formatCurrency(totalAmount, currency)}
                </div>
              </div>
            </div>
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
              {nextStep.settlement_period_id && (
                <div className="mt-2 text-xs text-muted-foreground">
                  Settlement period:{' '}
                  <span className="font-mono">{nextStep.settlement_period_id}</span>
                </div>
              )}
              {nextStep.notes && (
                <div className="mt-3 text-sm text-muted-foreground">{nextStep.notes}</div>
              )}
              <div className="mt-3">
                <StatusPill tone="warning">Requires human approval</StatusPill>
              </div>
            </div>

            {artifact.approval_instructions && (
              <div className="mt-4 rounded-2xl border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                {artifact.approval_instructions}
              </div>
            )}

            {/* Settlement action section */}
            {(isCreateSettlement || isApproveSettlement) && !actionResult && (
              <div className="mt-4 space-y-3">
                <div className="rounded-xl border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-warning">
                  <p className="font-semibold">Before confirming:</p>
                  {isCreateSettlement ? (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-warning/80">
                      <li>
                        Creates a <strong>draft settlement period only</strong>.
                      </li>
                      <li>Does not approve or compute claims.</li>
                      <li>
                        Does not execute settlement. No wallet, MozaiksPay, or Stripe calls.
                      </li>
                    </ul>
                  ) : (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-warning/80">
                      <li>Computes and writes distribution claims — this step is significant.</li>
                      <li>Does not execute settlement or call wallet/payout.</li>
                      <li>Payout execution remains deferred.</li>
                    </ul>
                  )}
                </div>

                <label className="flex cursor-pointer select-none items-start gap-3">
                  <input
                    type="checkbox"
                    checked={confirmAction}
                    onChange={(e) => setConfirmAction(e.target.checked)}
                    data-testid="confirm-settlement-action-checkbox"
                    className="mt-0.5 shrink-0 accent-primary"
                  />
                  <span className="text-sm text-foreground">
                    {isCreateSettlement
                      ? 'I have reviewed this artifact and want to create a draft settlement period.'
                      : 'I have reviewed this artifact and want to approve the settlement period and compute claims.'}
                  </span>
                </label>

                {actionError && (
                  <p
                    className="text-sm text-destructive"
                    data-testid="settlement-action-error"
                  >
                    {actionError}
                  </p>
                )}

                <Button
                  disabled={!confirmAction || performingAction}
                  data-testid="settlement-action-btn"
                  onClick={handleSettlementAction}
                >
                  {performingAction
                    ? isCreateSettlement
                      ? 'Creating...'
                      : 'Approving...'
                    : isCreateSettlement
                      ? 'Create Settlement Period'
                      : 'Approve Settlement Period'}
                </Button>
              </div>
            )}

            {actionResult && (
              <div
                className="mt-4 rounded-xl border border-border bg-muted/10 px-4 py-3 text-sm"
                data-testid="settlement-action-success"
              >
                <p className="font-semibold text-foreground">
                  {isCreateSettlement ? 'Settlement period created.' : 'Settlement period approved.'}
                </p>
                {actionResult.settlement_period_id && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Period ID:{' '}
                    <span className="font-mono">{actionResult.settlement_period_id}</span>
                    {actionResult.status ? ` · Status: ${actionResult.status}` : ''}
                    {actionResult.claims_created != null
                      ? ` · Claims created: ${actionResult.claims_created}`
                      : ''}
                  </p>
                )}
                {isApproveSettlement && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Settlement execution remains deferred. No payout has been initiated.
                  </p>
                )}
              </div>
            )}
          </Panel>
        </div>

        {/* ---- Distribution preview ---- */}
        <Panel
          title={`Distribution preview — ${distributionPreview.length} member${distributionPreview.length !== 1 ? 's' : ''}`}
          subtitle="Advisory only. Actual distribution is recomputed live on approve_settlement_period."
        >
          <ResourceList
            items={distributionPreview}
            columns={previewColumns}
            getItemId={(item, idx) => item.user_id || `preview-${idx}`}
            empty={{
              title: 'No preview entries',
              description: 'No distribution preview was generated for this review.',
            }}
          />
        </Panel>

        {/* ---- Assumptions / risks ---- */}
        {(artifact.assumptions?.length > 0 || artifact.risks?.length > 0) && (
          <div className="grid gap-6 xl:grid-cols-2">
            {artifact.assumptions?.length > 0 && (
              <Panel
                title="Assumptions"
                subtitle="Key assumptions this preview is based on."
              >
                <ul className="flex flex-col gap-1.5">
                  {artifact.assumptions.map((assumption, idx) => (
                    <li key={idx} className="flex gap-2 text-sm">
                      <span className="shrink-0 select-none text-muted-foreground">•</span>
                      <span className="text-foreground">{assumption}</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}

            {artifact.risks?.length > 0 && (
              <Panel
                title="Risks"
                subtitle="Open questions and concerns for reviewer evaluation."
              >
                <ul className="flex flex-col gap-1.5">
                  {artifact.risks.map((risk, idx) => (
                    <li key={idx} className="flex gap-2 text-sm">
                      <span className="shrink-0 select-none font-bold text-warning">!</span>
                      <span className="text-foreground">{risk}</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        )}
      </div>
    </WorkspaceLayout>
  )
}
