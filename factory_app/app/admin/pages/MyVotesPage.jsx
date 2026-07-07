/**
 * MyVotesPage — Open governance proposals across the user's communities.
 *
 * Shows all proposals currently open for voting across communities the user
 * belongs to. Clicking "Vote" navigates to the proposal detail page where
 * the vote is cast.
 *
 * Route: /votes
 */

import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Button,
  ErrorState,
  LoadingState,
  Panel,
  ResourceList,
  StatusPill,
} from '@mozaiks/chat-ui/ui'
import { useChatUI } from '@mozaiks/chat-ui/context/ChatUIContext.jsx'

import {
  getUserId,
  formatShortDate,
  humanizeLabel,
  proposalStatusTone,
  callModuleAction,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MyVotesPage() {
  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [proposals, setProposals] = useState([])
  const [communityToAppMap, setCommunityToAppMap] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const [communitiesResult, proposalsResult] = await Promise.all([
        callModuleAction('community_membership', 'list_communities', { limit: 200 }),
        callModuleAction('community_governance', 'list_proposals', {
          status: 'open',
          limit: 200,
        }),
      ])

      // Build a map of community_id → app_id for navigation
      const appMap = {}
      for (const community of communitiesResult.communities || []) {
        if (community.community_id && community.app_id) {
          appMap[community.community_id] = community.app_id
        }
      }
      setCommunityToAppMap(appMap)
      setProposals(proposalsResult.proposals || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Proposals could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [currentUserId])

  const proposalColumns = [
    {
      id: 'title',
      header: 'Proposal',
      render: (proposal) => (
        <div>
          <div className="font-semibold text-foreground">{proposal.title}</div>
          <div className="mt-1 text-sm text-muted-foreground">
            {proposal.community_name || proposal.community_id} ·{' '}
            {humanizeLabel(proposal.proposal_type)} · Created{' '}
            {formatShortDate(proposal.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (proposal) => (
        <StatusPill tone={proposalStatusTone(proposal.status)}>
          {humanizeLabel(proposal.status)}
        </StatusPill>
      ),
    },
    {
      id: 'tally',
      header: 'Tally',
      render: (proposal) => (
        <div className="text-sm text-muted-foreground">
          Yes {proposal.yes_weight ?? 0} · No {proposal.no_weight ?? 0} · Abstain{' '}
          {proposal.abstain_weight ?? 0}
        </div>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      width: '10rem',
      cellClassName: 'text-right',
      render: (proposal) => {
        const appId = communityToAppMap[proposal.community_id]
        if (!appId) return null
        return (
          <Button variant="outline" asChild>
            <Link to={`/apps/${appId}/governance/${proposal.proposal_id}`}>Vote</Link>
          </Button>
        )
      },
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading open proposals..." />
  if (error) return <ErrorState title="Proposals unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        {/* ---- Page header ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Account
              </div>
              <h1 className="mt-1 text-2xl font-semibold text-foreground">My Votes</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Open proposals from your communities. Click Vote to cast your ballot on the
                proposal detail page.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link to="/communities">My Communities</Link>
              </Button>
            </div>
          </div>
        </div>

        {/* ---- Open proposals ---- */}
        <Panel
          title="Open proposals"
          subtitle="Proposals currently open for voting across your communities."
        >
          <ResourceList
            items={proposals}
            columns={proposalColumns}
            getItemId={(item, idx) => item.proposal_id ?? `proposal-${idx}`}
            empty={{
              title: 'No open proposals',
              description:
                'There are no proposals currently open for voting in your communities.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
