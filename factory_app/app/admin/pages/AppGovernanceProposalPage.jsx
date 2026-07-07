/**
 * AppGovernanceProposalPage — Single governance proposal detail + voting.
 *
 * Shows proposal metadata, the immutable vote snapshot, allows eligible
 * members to cast votes, manage delegation, and (for managers) calculate
 * the outcome.
 *
 * Route: /apps/:appId/governance/:proposalId
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
import { useChatUI } from '@mozaiks/chat-ui/context/ChatUIContext.jsx'

import {
  getUserId,
  formatShortDateTime,
  humanizeLabel,
  proposalStatusTone,
  outcomeStatusTone,
  delegationStatusTone,
  assertNotPendingMember,
  callModuleAction,
  withCommunityParam,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AppGovernanceProposalPage() {
  const { appId, proposalId } = useParams()
  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [app, setApp] = useState(null)
  const [summary, setSummary] = useState(null)
  const [proposal, setProposal] = useState(null)
  const [snapshot, setSnapshot] = useState(null)
  const [votes, setVotes] = useState([])
  const [delegations, setDelegations] = useState([])

  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  // Vote form state
  const [voteChoice, setVoteChoice] = useState('yes')
  // Delegation form state
  const [delegateUserId, setDelegateUserId] = useState('')

  async function loadData() {
    if (!appId || !proposalId) return
    setLoading(true)
    setPageError(null)
    try {
      // Load app community summary (for app name)
      const summaryResult = await callModuleAction(
        'community_membership',
        'get_app_community_summary',
        { app_id: appId },
      )
      const validatedSummary = assertNotPendingMember(summaryResult?.summary || null)
      setApp({ app_id: validatedSummary?.app_id, name: validatedSummary?.app_name })
      setSummary(validatedSummary)

      // Load proposal detail
      const proposalResult = await callModuleAction('community_governance', 'get_proposal', {
        proposal_id: proposalId,
      })
      const proposalData = proposalResult.proposal
      setProposal(proposalData)

      // Load vote snapshot
      const snapshotResult = proposalData
        ? await callModuleAction('community_governance', 'get_vote_snapshot', {
            proposal_id: proposalId,
          })
        : { snapshot: null }
      setSnapshot(snapshotResult.snapshot || proposalResult.snapshot || null)
      setVotes(proposalResult.votes || [])

      // Load delegations for the proposal's community
      let loadedDelegations = []
      if (proposalData?.community_id) {
        const delegationsResult = await callModuleAction(
          'community_governance',
          'list_delegations',
          { community_id: proposalData.community_id, limit: 200 },
        )
        loadedDelegations = delegationsResult.delegations || []
      }
      setDelegations(loadedDelegations)
      setActionError(null)
    } catch (err) {
      setPageError(
        err instanceof Error ? err.message : 'Proposal detail could not be loaded.',
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [appId, proposalId])

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------

  const myVote = useMemo(
    () => votes.find((v) => v.voter_user_id === currentUserId) || null,
    [currentUserId, votes],
  )

  const myOutgoingDelegation = useMemo(
    () =>
      delegations.find(
        (d) => d.delegator_user_id === currentUserId && d.status === 'active',
      ) || null,
    [currentUserId, delegations],
  )

  const eligibleVoters = snapshot?.eligible_voters || []
  const myVotingPower = snapshot?.voting_power_by_user?.[currentUserId] || 0
  const canManageGovernance = summary?.can_open_close_proposal === true
  const canVote =
    summary?.can_vote !== false &&
    proposal?.status === 'open' &&
    eligibleVoters.includes(currentUserId) &&
    myVotingPower > 0 &&
    !myVote

  const summaryItems = [
    {
      id: 'proposal-status',
      label: 'Status',
      value: humanizeLabel(proposal?.status || 'unknown'),
      detail: proposal?.community_name || 'Community proposal',
    },
    {
      id: 'outcome',
      label: 'Outcome',
      value: humanizeLabel(proposal?.outcome_status || 'pending'),
      detail: proposal?.outcome_status
        ? 'Calculated from immutable snapshot'
        : 'Outcome not calculated yet',
    },
    {
      id: 'cast-power',
      label: 'Cast Power',
      value: String(proposal?.outcome?.total_cast_power ?? 0),
      detail: 'Total power already cast',
    },
    {
      id: 'total-power',
      label: 'Snapshot Power',
      value: String(snapshot?.total_power ?? proposal?.snapshot_total_power ?? 0),
      detail: 'Immutable proposal snapshot total',
    },
  ]

  const voteColumns = [
    {
      id: 'voter_user_id',
      header: 'Voter',
      render: (vote) => (
        <div>
          <div className="font-semibold text-foreground">{vote.voter_user_id}</div>
          <div className="mt-1 text-sm text-muted-foreground">
            {formatShortDateTime(vote.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'choice',
      header: 'Choice',
      width: '10rem',
      render: (vote) => (
        <StatusPill
          tone={
            vote.choice === 'yes'
              ? 'success'
              : vote.choice === 'no'
                ? 'destructive'
                : 'muted'
          }
        >
          {humanizeLabel(vote.choice)}
        </StatusPill>
      ),
    },
    {
      id: 'weight',
      header: 'Weight',
      width: '8rem',
      render: (vote) => (
        <span className="font-semibold text-foreground">{vote.weight}</span>
      ),
    },
    {
      id: 'source',
      header: 'Source',
      render: (vote) => (
        <span className="text-sm text-muted-foreground">{vote.voting_power_source}</span>
      ),
    },
  ]

  const delegationColumns = [
    {
      id: 'delegator_user_id',
      header: 'Delegator',
      render: (d) => d.delegator_user_id,
    },
    {
      id: 'delegate_user_id',
      header: 'Delegate',
      render: (d) => d.delegate_user_id,
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (d) => (
        <StatusPill tone={delegationStatusTone(d.status)}>{humanizeLabel(d.status)}</StatusPill>
      ),
    },
  ]

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleCastVote(event) {
    event.preventDefault()
    if (!proposalId) return
    setActionBusy('vote')
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'cast_vote', {
        proposal_id: proposalId,
        choice: voteChoice,
      })
      await loadData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Vote could not be cast.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleDelegate(event) {
    event.preventDefault()
    if (!proposal?.community_id) return
    setActionBusy('delegate')
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'delegate_vote', {
        community_id: proposal.community_id,
        delegate_user_id: delegateUserId.trim(),
      })
      setDelegateUserId('')
      await loadData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Delegation could not be created.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleRevokeDelegation() {
    if (!proposal?.community_id) return
    setActionBusy('revoke-delegation')
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'revoke_delegation', {
        community_id: proposal.community_id,
      })
      await loadData()
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : 'Delegation could not be revoked.',
      )
    } finally {
      setActionBusy('')
    }
  }

  async function handleCalculateOutcome() {
    if (!proposalId) return
    setActionBusy('outcome')
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'calculate_outcome', {
        proposal_id: proposalId,
      })
      await loadData()
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : 'Outcome could not be calculated.',
      )
    } finally {
      setActionBusy('')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading proposal detail..." />
  if (pageError) return <ErrorState title="Proposal unavailable" message={pageError} />
  if (!proposal) return <InlineEmptyState title="Proposal not found" />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        {/* ---- Page header ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                App Studio
              </div>
              <h1 className="mt-1 text-2xl font-semibold text-foreground">{proposal.title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {proposal.community_name}
                {app?.name ? ` · ${app.name}` : ''}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatusPill tone={proposalStatusTone(proposal.status)}>
                  {humanizeLabel(proposal.status)}
                </StatusPill>
                {proposal.outcome_status && (
                  <StatusPill tone={outcomeStatusTone(proposal.outcome_status)}>
                    {humanizeLabel(proposal.outcome_status)}
                  </StatusPill>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link to={withCommunityParam(`/apps/${appId}/governance`, proposal.community_id)}>
                  Back to governance
                </Link>
              </Button>

              {proposal.status === 'closed' && !proposal.outcome_status && (
                <Button
                  disabled={!canManageGovernance || actionBusy === 'outcome'}
                  onClick={handleCalculateOutcome}
                >
                  {actionBusy === 'outcome' ? 'Calculating...' : 'Calculate outcome'}
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* ---- Action error ---- */}
        {actionError && (
          <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
            <div className="text-sm text-destructive">{actionError}</div>
          </div>
        )}

        {/* ---- Summary strip ---- */}
        <Panel
          title="Proposal summary"
          subtitle="Snapshot-backed governance details for this proposal."
        >
          <SummaryStrip items={summaryItems} />
        </Panel>

        {/* ---- Proposal detail + snapshot ---- */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel
            title="Proposal detail"
            subtitle="Proposal content and lifecycle timestamps."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Type
                </div>
                <div className="mt-2 text-lg font-semibold text-foreground">
                  {humanizeLabel(proposal.proposal_type)}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  Created {formatShortDateTime(proposal.created_at)}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Created by
                </div>
                <div className="mt-2 text-lg font-semibold text-foreground">
                  {proposal.created_by}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  Opened {formatShortDateTime(proposal.opened_at)} · Closed{' '}
                  {formatShortDateTime(proposal.closed_at)}
                </div>
              </div>
            </div>

            {proposal.description ? (
              <div className="mt-4 rounded-2xl border border-border bg-muted/20 px-4 py-4 text-sm text-foreground">
                {proposal.description}
              </div>
            ) : (
              <div className="mt-4 text-sm text-muted-foreground">
                No proposal description was provided.
              </div>
            )}
          </Panel>

          <Panel
            title="Immutable vote snapshot"
            subtitle="Snapshot values remain fixed after the proposal opens."
          >
            {snapshot ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Snapshot id
                  </div>
                  <div className="mt-2 text-sm font-semibold text-foreground">
                    {snapshot.snapshot_id}
                  </div>
                  <div className="mt-2 text-sm text-muted-foreground">
                    Created {formatShortDateTime(snapshot.created_at)}
                  </div>
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Voting power
                  </div>
                  <div className="mt-2 text-lg font-semibold text-foreground">
                    {snapshot.total_power}
                  </div>
                  <div className="mt-2 text-sm text-muted-foreground">
                    Scoring {snapshot.scoring_version}
                  </div>
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Eligible voters
                  </div>
                  <div className="mt-2 text-lg font-semibold text-foreground">
                    {(snapshot.eligible_voters || []).length}
                  </div>
                  <div className="mt-2 text-sm text-muted-foreground">
                    Direct-vote roster frozen at proposal open.
                  </div>
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Delegation edges
                  </div>
                  <div className="mt-2 text-lg font-semibold text-foreground">
                    {(snapshot.delegation_edges || []).length}
                  </div>
                  <div className="mt-2 text-sm text-muted-foreground">
                    Delegation changes affect future proposal snapshots only.
                  </div>
                </div>
              </div>
            ) : (
              <InlineEmptyState
                title="Snapshot unavailable"
                description="A snapshot is created once the proposal opens."
              />
            )}
          </Panel>
        </div>

        {/* ---- Participation + delegation ---- */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel
            title="Your participation"
            subtitle="Cast your vote if eligible and review whether you have already participated."
          >
            {proposal.status !== 'open' ? (
              <InlineEmptyState
                title="Voting closed"
                description="Votes can only be cast while the proposal is open."
              />
            ) : myVote ? (
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="flex flex-col gap-2">
                  <div className="text-sm font-semibold text-foreground">You already voted</div>
                  <div className="text-sm text-muted-foreground">
                    {humanizeLabel(myVote.choice)} with weight {myVote.weight}.
                  </div>
                </div>
              </div>
            ) : canVote ? (
              <form className="grid gap-4" onSubmit={handleCastVote}>
                <div className="rounded-2xl border border-border bg-muted/20 px-4 py-4 text-sm text-muted-foreground">
                  Voting power for this snapshot:{' '}
                  <span className="font-semibold text-foreground">{myVotingPower}</span>
                </div>

                <label className="grid gap-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Vote choice</span>
                  <select
                    value={voteChoice}
                    onChange={(e) => setVoteChoice(e.target.value)}
                    className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
                  >
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                    <option value="abstain">Abstain</option>
                  </select>
                </label>

                <div>
                  <Button type="submit" disabled={actionBusy === 'vote'}>
                    {actionBusy === 'vote' ? 'Casting...' : 'Cast vote'}
                  </Button>
                </div>
              </form>
            ) : (
              <InlineEmptyState
                title="Vote unavailable"
                description={
                  eligibleVoters.includes(currentUserId)
                    ? 'Delegated voters cannot cast a vote for this snapshot because their voting power is already transferred.'
                    : 'You are not an eligible voter for this proposal snapshot.'
                }
              />
            )}
          </Panel>

          <Panel
            title="Delegation"
            subtitle="Delegation is non-transitive in Phase 1 and affects future proposal snapshots only."
          >
            <div className="rounded-2xl border border-border bg-muted/20 px-4 py-4 text-sm text-muted-foreground">
              Delegation changes affect future proposal snapshots only.
            </div>

            {myOutgoingDelegation ? (
              <div className="mt-4 grid gap-4">
                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-sm font-semibold text-foreground">
                    Current delegation
                  </div>
                  <div className="mt-2 text-sm text-muted-foreground">
                    You delegate to{' '}
                    <span className="font-semibold text-foreground">
                      {myOutgoingDelegation.delegate_user_id}
                    </span>
                    .
                  </div>
                  <div className="mt-3">
                    <Button
                      variant="outline"
                      disabled={actionBusy === 'revoke-delegation'}
                      onClick={handleRevokeDelegation}
                    >
                      {actionBusy === 'revoke-delegation' ? 'Revoking...' : 'Revoke delegation'}
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <form className="mt-4 grid gap-4" onSubmit={handleDelegate}>
                <label className="grid gap-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Delegate user id</span>
                  <input
                    type="text"
                    value={delegateUserId}
                    onChange={(e) => setDelegateUserId(e.target.value)}
                    className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                    placeholder="community-admin-2"
                    required
                  />
                </label>

                <div>
                  <Button type="submit" disabled={actionBusy === 'delegate'}>
                    {actionBusy === 'delegate' ? 'Saving...' : 'Delegate vote'}
                  </Button>
                </div>
              </form>
            )}
          </Panel>
        </div>

        {/* ---- Votes ---- */}
        <Panel
          title="Votes"
          subtitle="Recorded proposal votes and immutable snapshot-weight sources."
        >
          <ResourceList
            items={votes}
            columns={voteColumns}
            getItemId={(item, idx) => item.vote_id ?? `vote-${idx}`}
            empty={{
              title: 'No votes yet',
              description:
                'Votes appear here once members cast them while the proposal is open.',
            }}
          />
        </Panel>

        {/* ---- Delegation registry ---- */}
        <Panel
          title="Delegation registry"
          subtitle="Current delegation records visible for this proposal community."
        >
          <ResourceList
            items={delegations}
            columns={delegationColumns}
            getItemId={(item, idx) => item.delegation_id ?? `delegation-${idx}`}
            empty={{
              title: 'No delegations yet',
              description:
                'Delegation records will appear here when members delegate future proposal voting power.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
