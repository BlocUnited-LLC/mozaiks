/**
 * AppCommunityPage — App Studio community management surface.
 *
 * Shows the app-scoped community roster, allows the owner to create the
 * first community, and navigates to the Collaborators / Governance sub-pages.
 *
 * Route: /apps/:appId/community
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
  memberStatusTone,
  isAdminRole,
  loadAppCommunityData,
  callModuleAction,
  withCommunityParam,
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

export default function AppCommunityPage() {
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
  const [selectedCommunityId, setSelectedCommunityId] = useState(initialCommunityId)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  // Create community form state
  const [communityName, setCommunityName] = useState('')
  const [communityDescription, setCommunityDescription] = useState('')

  // Member search state
  const [memberSearch, setMemberSearch] = useState('')

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
    setError(null)
    try {
      const bundle = await loadAppCommunityData(appId, communityId)
      setApp(bundle.app)
      setSummary(bundle.summary)
      setCommunities(bundle.communities)
      setCommunity(bundle.community)

      // Sync the URL param if the loader resolved to a different community
      if (bundle.selectedCommunityId !== communityId) {
        updateCommunityParam(bundle.selectedCommunityId)
      }

      if (!bundle.community) {
        setMembers([])
        setActionError(null)
        return
      }

      const membersResult = await callModuleAction('community_membership', 'list_members', {
        community_id: bundle.community.community_id,
        limit: 200,
      })
      setMembers(membersResult.members || [])
      setActionError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Community could not be loaded.')
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

  // Current user's membership entry
  const myMember = useMemo(
    () => members.find((m) => m.user_id === currentUserId) || null,
    [currentUserId, members],
  )

  // Filtered member list
  const filteredMembers = useMemo(() => {
    const search = memberSearch.trim().toLowerCase()
    if (!search) return members
    return members.filter((m) =>
      `${m.user_id ?? ''} ${m.role ?? ''} ${m.status ?? ''}`.toLowerCase().includes(search),
    )
  }, [members, memberSearch])

  // Summary strip items
  const summaryItems = [
    {
      id: 'community-access',
      label: 'Community Access',
      value: summary?.viewer_role ? humanizeLabel(summary.viewer_role) : 'Owner view',
      detail: summary?.can_manage_members
        ? 'Can manage members'
        : summary?.viewer_membership_status === 'none'
          ? 'No community exists yet'
          : 'Read-only community view',
    },
    {
      id: 'community-count',
      label: 'Communities',
      value: String(communities.length),
      detail: 'App-scoped community records',
    },
    {
      id: 'member-count',
      label: 'Members',
      value: String(members.length),
      detail: community ? `${community.name} roster` : 'Create a community first',
    },
    {
      id: 'my-role',
      label: 'Your Role',
      value: myMember ? humanizeLabel(myMember.role) : 'Not a member',
      detail:
        myMember && isAdminRole(myMember.role)
          ? 'Can manage members'
          : 'Read-only community view',
    },
  ]

  // Member roster columns
  const memberColumns = [
    {
      id: 'user_id',
      header: 'Member',
      render: (member) => (
        <div>
          <div className="font-semibold text-foreground">{member.user_id}</div>
          <div className="mt-1 text-sm text-muted-foreground">
            Joined {formatShortDate(member.joined_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'role',
      header: 'Role',
      width: '10rem',
      render: (member) => (
        <StatusPill tone="default">{humanizeLabel(member.role)}</StatusPill>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (member) => (
        <StatusPill tone={memberStatusTone(member.status)}>
          {humanizeLabel(member.status)}
        </StatusPill>
      ),
    },
    {
      id: 'updated_at',
      header: 'Updated',
      width: '12rem',
      render: (member) => (
        <span className="text-sm text-muted-foreground">
          {formatShortDateTime(member.updated_at)}
        </span>
      ),
    },
  ]

  async function handleCreateCommunity(event) {
    event.preventDefault()
    if (!appId) return
    setActionBusy('create')
    setActionError(null)
    try {
      const result = await callModuleAction('community_membership', 'create_community', {
        name: communityName,
        description: communityDescription,
        app_id: appId,
      })
      setCommunityName('')
      setCommunityDescription('')
      const newCommunityId = result.community?.community_id || ''
      updateCommunityParam(newCommunityId)
      await loadData(newCommunityId)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Community could not be created.')
    } finally {
      setActionBusy('')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading community..." />
  if (error) return <ErrorState title="Community unavailable" message={error} />

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
              <h1 className="mt-1 text-2xl font-semibold text-foreground">Community</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Manage app-scoped communities, review member roster, and move into
                collaborators or governance.
              </p>
              {app?.name && (
                <p className="mt-3 text-sm text-muted-foreground">
                  {app.name}
                  {summary?.viewer_role ? ` · ${humanizeLabel(summary.viewer_role)}` : ''}
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
                {community ? (
                  <Button variant="outline" asChild>
                    <Link to={buildCommunitySubPageUrl(appId, 'collaborators', community.community_id)}>
                      Collaborators
                    </Link>
                  </Button>
                ) : (
                  <Button variant="outline" disabled>
                    Collaborators
                  </Button>
                )}

                {community ? (
                  <Button asChild>
                    <Link to={buildCommunitySubPageUrl(appId, 'governance', community.community_id)}>
                      Governance
                    </Link>
                  </Button>
                ) : (
                  <Button disabled>Governance</Button>
                )}
              </div>
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
          title="Community overview"
          subtitle="App owners and community admins manage the app-scoped community here."
        >
          <SummaryStrip items={summaryItems} />
        </Panel>

        {/* ---- Community detail + member roster ---- */}
        {community ? (
          <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <Panel
              title="Selected community"
              subtitle="Community metadata and current operating status."
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Name
                  </div>
                  <div className="mt-2 text-lg font-semibold text-foreground">
                    {community.name}
                  </div>
                  {community.description && (
                    <p className="mt-2 text-sm text-muted-foreground">
                      {community.description}
                    </p>
                  )}
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Status
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <StatusPill tone={memberStatusTone(community.status)}>
                      {humanizeLabel(community.status)}
                    </StatusPill>
                  </div>
                  <div className="mt-3 text-sm text-muted-foreground">
                    Created {formatShortDateTime(community.created_at)}
                  </div>
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    App scope
                  </div>
                  <div className="mt-2 text-sm text-foreground">
                    {community.app_id || appId}
                  </div>
                  <div className="mt-3 text-sm text-muted-foreground">
                    Tenant {community.tenant_id || 'Not set'}
                  </div>
                </div>

                <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Your access
                  </div>
                  <div className="mt-2 text-sm text-foreground">
                    {myMember ? humanizeLabel(myMember.role) : 'Not in roster'}
                  </div>
                  <div className="mt-3 text-sm text-muted-foreground">
                    {myMember && isAdminRole(myMember.role)
                      ? 'Can manage collaborators and community state.'
                      : 'Read-only access from current membership.'}
                  </div>
                </div>
              </div>
            </Panel>

            <Panel
              title="Member roster"
              subtitle="Active community members for the selected app scope."
            >
              <CollectionToolbar
                searchValue={memberSearch}
                onSearchChange={setMemberSearch}
                searchPlaceholder="Search members..."
              />
              <ResourceList
                className="mt-4"
                items={filteredMembers}
                columns={memberColumns}
                getItemId={(item, idx) => item.member_id ?? `member-${idx}`}
                empty={{
                  title: 'No members yet',
                  description:
                    'Member records will appear here once the community has participants.',
                }}
              />
            </Panel>
          </div>
        ) : (
          /* ---- Create first community ---- */
          <Panel
            title="Create the first community"
            subtitle="This app does not have a community yet. Creating one also adds you as the initial owner."
          >
            <InlineEmptyState
              title="No community yet"
              description="Create the app's first hosted community to unlock collaborators and governance."
            />

            <form
              className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]"
              onSubmit={handleCreateCommunity}
            >
              <div className="grid gap-4">
                <label className="grid gap-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Community name</span>
                  <input
                    type="text"
                    value={communityName}
                    onChange={(e) => setCommunityName(e.target.value)}
                    className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                    placeholder="App builders"
                    required
                  />
                </label>

                <label className="grid gap-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Description</span>
                  <textarea
                    value={communityDescription}
                    onChange={(e) => setCommunityDescription(e.target.value)}
                    className="min-h-28 rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                    placeholder="Who this community is for and how it participates."
                  />
                </label>
              </div>

              <div className="flex items-end">
                <Button type="submit" disabled={actionBusy === 'create'}>
                  {actionBusy === 'create' ? 'Creating...' : 'Create community'}
                </Button>
              </div>
            </form>
          </Panel>
        )}
      </div>
    </WorkspaceLayout>
  )
}
