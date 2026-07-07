/**
 * AppGovernancePage — App Studio governance surface.
 *
 * Lists governance proposals for the app-scoped community and allows
 * authorized managers to create proposals, open / close voting, and
 * calculate immutable outcomes.
 *
 * Route: /apps/:appId/governance
 */

import { useState, useEffect, useMemo } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Button,
  CollectionToolbar,
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
  formatShortDate,
  formatShortDateTime,
  humanizeLabel,
  proposalStatusTone,
  outcomeStatusTone,
  isAdminRole,
  isContributorRole,
  loadAppCommunityData,
  callModuleAction,
  withCommunityParam,
  PROPOSAL_TYPES,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildCommunitySubPageUrl(appId, section, communityId) {
  return withCommunityParam(`/apps/${appId}/${section}`, communityId)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AppGovernancePage() {
  const { appId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialCommunityId = searchParams.get('community') || ''

  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [app, setApp] = useState(null)
  const [summary, setSummary] = useState(null)
  const [communities, setCommunities] = useState([])
  const [community, setCommunity] = useState(null)
  const [members, setMembers] = useState([])
  const [proposals, setProposals] = useState([])
  const [selectedCommunityId, setSelectedCommunityId] = useState(initialCommunityId)

  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  // Create proposal form state
  const [proposalTitle, setProposalTitle] = useState('')
  const [proposalDescription, setProposalDescription] = useState('')
  const [proposalType, setProposalType] = useState('general')

  // Proposal list search state
  const [proposalSearch, setProposalSearch] = useState('')

  function updateCommunityParam(id) {
    setSelectedCommunityId(id)
    const next = new URLSearchParams(searchParams)
    if (id) {
      next.set('community', id)
    } else {
      next.delete('community')
    }
    setSearchParams(next, { replace: true })
  }

  async function loadData(communityId = selectedCommunityId) {
    if (!appId) return
    setLoading(true)
    setPageError(null)
    try {
      const bundle = await loadAppCommunityData(appId, communityId)
      setApp(bundle.app)
      setSummary(bundle.summary)
      setCommunities(bundle.communities)
      setCommunity(bundle.community)

      if (bundle.selectedCommunityId !== communityId) {
        updateCommunityParam(bundle.selectedCommunityId)
      }

      if (!bundle.community) {
        setMembers([])
        setProposals([])
        return
      }

      const [membersResult, proposalsResult] = await Promise.all([
        callModuleAction('community_membership', 'list_members', {
          community_id: bundle.community.community_id,
          limit: 200,
        }),
        callModuleAction('community_governance', 'list_proposals', {
          community_id: bundle.community.community_id,
          limit: 200,
        }),
      ])
      setMembers(membersResult.members || [])
      setProposals(proposalsResult.proposals || [])
      setActionError(null)
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Governance could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setSelectedCommunityId(initialCommunityId)
  }, [initialCommunityId])

  useEffect(() => {
    loadData(initialCommunityId)
  }, [appId, initialCommunityId])

  // Viewer permissions derived from summary / member role
  const myMember = useMemo(
    () => members.find((m) => m.user_id === currentUserId) || null,
    [currentUserId, members],
  )
  const canManageGovernance =
    summary?.can_manage_governance ?? isAdminRole(myMember?.role)
  const canCreateProposal =
    summary?.can_create_proposal ?? isContributorRole(myMember?.role)

  // Filtered proposal list
  const filteredProposals = useMemo(() => {
    const search = proposalSearch.trim().toLowerCase()
    if (!search) return proposals
    return proposals.filter((p) =>
      `${p.title ?? ''} ${p.proposal_type ?? ''} ${p.status ?? ''} ${p.outcome_status ?? ''}`
        .toLowerCase()
        .includes(search),
    )
  }, [proposals, proposalSearch])

  // Proposal stats
  const stats = {
    total: proposals.length,
    draft: proposals.filter((p) => p.status === 'draft').length,
    open: proposals.filter((p) => p.status === 'open').length,
    closed: proposals.filter((p) => p.status === 'closed').length,
  }

  const summaryItems = [
    { id: 'total', label: 'Proposals', value: String(stats.total), detail: 'All proposal states' },
    { id: 'draft', label: 'Draft', value: String(stats.draft), detail: 'Ready to open' },
    { id: 'open', label: 'Open', value: String(stats.open), detail: 'Voting in progress' },
    {
      id: 'closed',
      label: 'Closed',
      value: String(stats.closed),
      detail: 'Awaiting or carrying outcome',
    },
  ]

  // Proposal list columns
  const proposalColumns = [
    {
      id: 'title',
      header: 'Proposal',
      render: (proposal) => (
        <div>
          <div className="font-semibold text-foreground">{proposal.title}</div>
          <div className="mt-1 text-sm text-muted-foreground">
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
      id: 'outcome',
      header: 'Outcome',
      width: '10rem',
      render: (proposal) =>
        proposal.outcome_status ? (
          <StatusPill tone={outcomeStatusTone(proposal.outcome_status)}>
            {humanizeLabel(proposal.outcome_status)}
          </StatusPill>
        ) : (
          <span className="text-sm text-muted-foreground">Pending</span>
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
      width: '22rem',
      cellClassName: 'text-right',
      render: (proposal) => (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" asChild>
            <Link to={`/apps/${appId}/governance/${proposal.proposal_id}`}>Details</Link>
          </Button>

          {proposal.status === 'draft' && (
            <Button
              variant="outline"
              disabled={!canManageGovernance || actionBusy === `open:${proposal.proposal_id}`}
              onClick={() => handleOpenProposal(proposal.proposal_id)}
            >
              Open
            </Button>
          )}

          {proposal.status === 'open' && (
            <Button
              variant="outline"
              disabled={!canManageGovernance || actionBusy === `close:${proposal.proposal_id}`}
              onClick={() => handleCloseProposal(proposal.proposal_id)}
            >
              Close
            </Button>
          )}

          {proposal.status === 'closed' && !proposal.outcome_status && (
            <Button
              disabled={!canManageGovernance || actionBusy === `outcome:${proposal.proposal_id}`}
              onClick={() => handleCalculateOutcome(proposal.proposal_id)}
            >
              Calculate outcome
            </Button>
          )}
        </div>
      ),
    },
  ]

  // ---------------------------------------------------------------------------
  // Proposal actions
  // ---------------------------------------------------------------------------

  async function handleCreateProposal(event) {
    event.preventDefault()
    if (!community) return
    setActionBusy('create')
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'create_proposal', {
        community_id: community.community_id,
        title: proposalTitle,
        description: proposalDescription,
        proposal_type: proposalType,
      })
      setProposalTitle('')
      setProposalDescription('')
      setProposalType('general')
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Proposal could not be created.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleOpenProposal(proposalId) {
    if (!community) return
    setActionBusy(`open:${proposalId}`)
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'open_proposal', { proposal_id: proposalId })
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Proposal could not be opened.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleCloseProposal(proposalId) {
    if (!community) return
    setActionBusy(`close:${proposalId}`)
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'close_proposal', { proposal_id: proposalId })
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Proposal could not be closed.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleCalculateOutcome(proposalId) {
    if (!community) return
    setActionBusy(`outcome:${proposalId}`)
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'calculate_outcome', {
        proposal_id: proposalId,
      })
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Outcome could not be calculated.')
    } finally {
      setActionBusy('')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading governance..." />
  if (pageError) return <ErrorState title="Governance unavailable" message={pageError} />

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
              <h1 className="mt-1 text-2xl font-semibold text-foreground">Governance</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Create proposals, open and close proposal voting, and calculate immutable
                outcomes for this app community.
              </p>
              {community && (
                <p className="mt-3 text-sm text-muted-foreground">
                  {community.name} · Created {formatShortDateTime(community.created_at)}
                </p>
              )}
            </div>

            <div className="flex flex-col gap-3 xl:min-w-[18rem]">
              {communities.length > 1 && (
                <label className="flex flex-col gap-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Community scope</span>
                  <select
                    value={selectedCommunityId}
                    onChange={(e) => updateCommunityParam(e.target.value)}
                    className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
                  >
                    {communities.map((c) => (
                      <option key={c.community_id} value={c.community_id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" asChild>
                  <Link to={buildCommunitySubPageUrl(appId, 'community', selectedCommunityId)}>
                    Community
                  </Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link to={buildCommunitySubPageUrl(appId, 'collaborators', selectedCommunityId)}>
                    Collaborators
                  </Link>
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* ---- Community exists branch ---- */}
        {community ? (
          <>
            {actionError && (
              <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
                <div className="text-sm text-destructive">{actionError}</div>
              </div>
            )}

            <Panel
              title="Governance summary"
              subtitle="Governance here is app-scoped and remains separate from financial execution authority."
            >
              <SummaryStrip items={summaryItems} />
            </Panel>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              {/* Create proposal form */}
              <Panel
                title="Create proposal"
                subtitle="Proposals stay in draft until an authorized manager opens voting."
              >
                {canCreateProposal ? (
                  <form className="grid gap-4" onSubmit={handleCreateProposal}>
                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Title</span>
                      <input
                        type="text"
                        value={proposalTitle}
                        onChange={(e) => setProposalTitle(e.target.value)}
                        className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                        placeholder="Approve release criteria"
                        required
                      />
                    </label>

                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Description</span>
                      <textarea
                        value={proposalDescription}
                        onChange={(e) => setProposalDescription(e.target.value)}
                        className="min-h-28 rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                        placeholder="Summarize the decision the community is being asked to make."
                      />
                    </label>

                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Proposal type</span>
                      <select
                        value={proposalType}
                        onChange={(e) => setProposalType(e.target.value)}
                        className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
                      >
                        {PROPOSAL_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {humanizeLabel(type)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <div>
                      <Button type="submit" disabled={actionBusy === 'create'}>
                        {actionBusy === 'create' ? 'Creating...' : 'Create proposal'}
                      </Button>
                    </div>
                  </form>
                ) : (
                  <InlineEmptyState
                    title="Read-only proposal view"
                    description="Only proposer roles can create new governance proposals for this community."
                  />
                )}
              </Panel>

              {/* Phase 1 governance settings */}
              <Panel
                title="Phase 1 governance settings"
                subtitle="These settings are fixed for the current hosted phase and remain non-financial."
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                      Quorum ratio
                    </div>
                    <div className="mt-2 text-lg font-semibold text-foreground">15%</div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      Minimum fraction of total voting power required for quorum.
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                      Scoring version
                    </div>
                    <div className="mt-2 text-lg font-semibold text-foreground">
                      role_weight.v1
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      Voting power remains role-weighted in Phase 1.
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                      Delegation model
                    </div>
                    <div className="mt-2 text-lg font-semibold text-foreground">Direct only</div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      Delegation is intentionally non-transitive in this phase.
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                      Financial boundary
                    </div>
                    <div className="mt-2 text-lg font-semibold text-foreground">
                      No money movement
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      Governance stays separate from financial execution and authority changes
                      remain proposal-only.
                    </div>
                  </div>
                </div>
              </Panel>
            </div>

            {/* Proposal list */}
            <Panel
              title="Proposal list"
              subtitle="Open, close, and calculate outcomes for proposals in the selected community."
            >
              <CollectionToolbar
                searchValue={proposalSearch}
                onSearchChange={setProposalSearch}
                searchPlaceholder="Search proposals..."
              />
              <ResourceList
                className="mt-4"
                items={filteredProposals}
                columns={proposalColumns}
                getItemId={(item, idx) => item.proposal_id ?? `proposal-${idx}`}
                empty={{
                  title: 'No proposals yet',
                  description: canCreateProposal
                    ? 'Create the first proposal for this app community.'
                    : 'Proposals will appear here once the community begins governing decisions.',
                }}
              />
            </Panel>
          </>
        ) : (
          /* ---- No community state ---- */
          <Panel
            title="No community selected"
            subtitle="Create a community before governing proposals."
          >
            <InlineEmptyState
              title="Create a community first"
              description="Governance opens after the app has an app-scoped community to govern."
              action={
                <Button asChild>
                  <Link to={`/apps/${appId}/community`}>Open community page</Link>
                </Button>
              }
            />
          </Panel>
        )}
      </div>
    </WorkspaceLayout>
  )
}
