/**
 * AppRevenueParticipationPage — Revenue participation artifacts surface.
 *
 * Lists RevenueParticipationDesignerWorkflow plan proposals and
 * RevenueDistributionReviewWorkflow distribution reviews for human operator
 * review. This page does NOT take any automated module actions.
 *
 * Route: /apps/:appId/revenue-participation
 */

import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Button,
  ErrorState,
  LoadingState,
  Panel,
  ResourceList,
  StatusPill,
} from '@mozaiks/chat-ui/ui'

import { callModuleAction } from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatShortDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function ReviewRequiredPill() {
  return <StatusPill tone="warning">Review required</StatusPill>
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AppRevenueParticipationPage() {
  const { appId } = useParams()

  const [proposals, setProposals] = useState([])
  const [reviews, setReviews] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function loadData() {
    if (!appId) return
    setLoading(true)
    setError(null)
    try {
      const [proposalsResult, reviewsResult] = await Promise.all([
        callModuleAction('community_revenue_participation', 'list_revenue_plan_proposals', {
          app_id: appId,
          limit: 50,
        }),
        callModuleAction('community_revenue_participation', 'list_distribution_reviews', {
          app_id: appId,
          limit: 50,
        }),
      ])
      setProposals(proposalsResult.proposals || [])
      setReviews(reviewsResult.reviews || [])
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Revenue participation data could not be loaded.',
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [appId])

  // ---------------------------------------------------------------------------
  // Column definitions
  // ---------------------------------------------------------------------------

  const proposalColumns = [
    {
      id: 'proposal_id',
      header: 'Proposal',
      render: (proposal) => {
        const docId = proposal.doc_id || proposal.proposal_id || ''
        return (
          <div>
            <div className="font-mono text-sm text-foreground">{docId || '—'}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              Community: {proposal.community_id || '—'}
            </div>
          </div>
        )
      },
    },
    {
      id: 'review',
      header: 'Review',
      width: '10rem',
      render: () => <ReviewRequiredPill />,
    },
    {
      id: 'created_at',
      header: 'Created',
      width: '10rem',
      render: (proposal) => (
        <span className="text-sm text-muted-foreground">
          {formatShortDate(proposal.created_at)}
        </span>
      ),
    },
    {
      id: 'action',
      header: '',
      width: '8rem',
      render: (proposal) => {
        const docId = proposal.doc_id || proposal.proposal_id || ''
        if (!docId) return null
        return (
          <Button variant="outline" size="sm" asChild>
            <Link to={`/apps/${appId}/revenue-participation/plan-proposals/${docId}`}>
              Review
            </Link>
          </Button>
        )
      },
    },
  ]

  const reviewColumns = [
    {
      id: 'review_id',
      header: 'Distribution Review',
      render: (review) => {
        const docId = review.doc_id || review.review_id || ''
        return (
          <div>
            <div className="font-mono text-sm text-foreground">{docId || '��'}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              Community: {review.community_id || '—'}
            </div>
          </div>
        )
      },
    },
    {
      id: 'review',
      header: 'Review',
      width: '10rem',
      render: () => <ReviewRequiredPill />,
    },
    {
      id: 'created_at',
      header: 'Created',
      width: '10rem',
      render: (review) => (
        <span className="text-sm text-muted-foreground">
          {formatShortDate(review.created_at)}
        </span>
      ),
    },
    {
      id: 'action',
      header: '',
      width: '8rem',
      render: (review) => {
        const docId = review.doc_id || review.review_id || ''
        if (!docId) return null
        return (
          <Button variant="outline" size="sm" asChild>
            <Link to={`/apps/${appId}/revenue-participation/distribution-reviews/${docId}`}>
              Review
            </Link>
          </Button>
        )
      },
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading revenue participation artifacts..." />
  if (error) return <ErrorState title="Revenue participation unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        {/* ---- Page header ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-5">
          <div className="flex flex-col gap-2 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                App Studio
              </div>
              <h1 className="mt-1 text-2xl font-semibold text-foreground">
                Revenue Participation
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Workflow-generated artifacts for human operator review. No module actions are
                taken automatically from this page.
              </p>
            </div>
          </div>
        </div>

        {/* ---- Plan proposals ---- */}
        <Panel
          title="Plan Proposals"
          subtitle="RevenueParticipationDesignerWorkflow artifacts. Each proposal requires human review before any plan action is taken."
        >
          <ResourceList
            items={proposals}
            columns={proposalColumns}
            getItemId={(item, idx) => item.doc_id || item.proposal_id || `proposal-${idx}`}
            empty={{
              title: 'No plan proposals yet',
              description:
                'Run the RevenueParticipationDesignerWorkflow to generate proposals for review.',
            }}
          />
        </Panel>

        {/* ---- Distribution reviews ---- */}
        <Panel
          title="Distribution Reviews"
          subtitle="RevenueDistributionReviewWorkflow artifacts. Each review is advisory — actual distribution is recomputed live on approval."
        >
          <ResourceList
            items={reviews}
            columns={reviewColumns}
            getItemId={(item, idx) => item.doc_id || item.review_id || `review-${idx}`}
            empty={{
              title: 'No distribution reviews yet',
              description:
                'Run the RevenueDistributionReviewWorkflow to generate distribution reviews for review.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
