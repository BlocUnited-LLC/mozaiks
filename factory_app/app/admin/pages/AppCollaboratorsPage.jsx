/**
 * AppCollaboratorsPage — App Studio collaborators surface.
 *
 * Allows owners and admins to invite collaborators, update member roles, and
 * remove access from the app-scoped community roster.
 *
 * Route: /apps/:appId/collaborators
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
  humanizeLabel,
  memberStatusTone,
  invitationStatusTone,
  isAdminRole,
  canManageMembers,
  loadAppCommunityData,
  callModuleAction,
  withCommunityParam,
  MEMBER_ROLES,
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

export default function AppCollaboratorsPage() {
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
  const [invitations, setInvitations] = useState([])
  const [selectedCommunityId, setSelectedCommunityId] = useState(initialCommunityId)

  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  // Newly created invitation token display
  const [latestInvitation, setLatestInvitation] = useState(null)

  // Invite form state
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteUserId, setInviteUserId] = useState('')
  const [inviteRole, setInviteRole] = useState('contributor')

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
        setInvitations([])
        return
      }

      const [membersResult, invitationsResult] = await Promise.all([
        callModuleAction('community_membership', 'list_members', {
          community_id: bundle.community.community_id,
          limit: 200,
        }),
        bundle.summary?.can_invite
          ? callModuleAction('community_membership', 'list_invitations', {
              community_id: bundle.community.community_id,
              limit: 200,
            })
          : Promise.resolve({ invitations: [] }),
      ])
      setMembers(membersResult.members || [])
      setInvitations(invitationsResult.invitations || [])
      setActionError(null)
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Collaborators could not be loaded.')
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

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------

  const myMember = useMemo(
    () => members.find((m) => m.user_id === currentUserId) || null,
    [currentUserId, members],
  )
  const canEdit =
    summary?.can_manage_members ?? canManageMembers(myMember?.role)

  const filteredMembers = useMemo(() => {
    const search = memberSearch.trim().toLowerCase()
    if (!search) return members
    return members.filter((m) =>
      `${m.user_id ?? ''} ${m.role ?? ''} ${m.status ?? ''}`.toLowerCase().includes(search),
    )
  }, [members, memberSearch])

  const pendingInvitations = invitations.filter((inv) => inv.status === 'pending')

  const summaryItems = [
    {
      id: 'community-status',
      label: 'Community Status',
      value: humanizeLabel(community?.status || 'none'),
      detail: community?.name || 'No selected community',
    },
    {
      id: 'member-count',
      label: 'Members',
      value: String(members.length),
      detail: 'Current roster size',
    },
    {
      id: 'pending-invites',
      label: 'Pending Invites',
      value: String(pendingInvitations.length),
      detail: 'Outstanding collaborator invitations',
    },
    {
      id: 'my-role',
      label: 'Your Role',
      value:
        summary?.viewer_role
          ? humanizeLabel(summary.viewer_role)
          : myMember
            ? humanizeLabel(myMember.role)
            : 'Not a member',
      detail: summary?.can_invite ? 'Can manage collaborators' : 'Member visibility only',
    },
  ]

  // ---------------------------------------------------------------------------
  // Column definitions
  // ---------------------------------------------------------------------------

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
      width: '12rem',
      render: (member) =>
        canEdit ? (
          <select
            value={member.role}
            disabled={actionBusy === `role:${member.member_id}`}
            onChange={(e) => handleUpdateRole(member, e.target.value)}
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
          >
            {MEMBER_ROLES.map((role) => (
              <option key={role} value={role}>
                {humanizeLabel(role)}
              </option>
            ))}
          </select>
        ) : (
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
      id: 'actions',
      header: 'Actions',
      width: '11rem',
      cellClassName: 'text-right',
      render: (member) => (
        <div className="flex justify-end">
          <Button
            variant="outline"
            disabled={
              !canEdit ||
              actionBusy === `remove:${member.member_id}` ||
              (member.user_id === currentUserId && member.role === 'owner')
            }
            onClick={() => handleRemoveMember(member)}
          >
            {member.user_id === currentUserId ? 'Leave' : 'Remove'}
          </Button>
        </div>
      ),
    },
  ]

  const invitationColumns = [
    {
      id: 'invitee',
      header: 'Invitee',
      render: (inv) => (
        <div>
          <div className="font-semibold text-foreground">
            {inv.email || inv.invitee_user_id || 'Direct invite'}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            Sent {formatShortDate(inv.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'role',
      header: 'Role',
      width: '10rem',
      render: (inv) => (
        <StatusPill tone="default">{humanizeLabel(inv.role)}</StatusPill>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (inv) => (
        <StatusPill tone={invitationStatusTone(inv.status)}>
          {humanizeLabel(inv.status)}
        </StatusPill>
      ),
    },
    {
      id: 'expires_at',
      header: 'Expires',
      width: '12rem',
      render: (inv) => (
        <span className="text-sm text-muted-foreground">{formatShortDate(inv.expires_at)}</span>
      ),
    },
  ]

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleCreateInvitation(event) {
    event.preventDefault()
    if (!community) return
    if (!inviteEmail.trim() && !inviteUserId.trim()) {
      setActionError('Provide either an invite email or invitee user id.')
      return
    }
    setActionBusy('invite')
    setActionError(null)
    try {
      const result = await callModuleAction('community_membership', 'create_invitation', {
        community_id: community.community_id,
        role: inviteRole,
        ...(inviteEmail.trim() ? { email: inviteEmail.trim() } : {}),
        ...(inviteUserId.trim() ? { invitee_user_id: inviteUserId.trim() } : {}),
      })
      setInviteEmail('')
      setInviteUserId('')
      setInviteRole('contributor')
      setLatestInvitation(result)
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Invitation could not be created.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleUpdateRole(member, newRole) {
    if (!community || newRole === member.role) return
    setActionBusy(`role:${member.member_id}`)
    setActionError(null)
    try {
      await callModuleAction('community_membership', 'update_member_role', {
        community_id: community.community_id,
        user_id: member.user_id,
        role: newRole,
      })
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Role could not be updated.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleRemoveMember(member) {
    if (!community) return
    setActionBusy(`remove:${member.member_id}`)
    setActionError(null)
    try {
      await callModuleAction('community_membership', 'remove_member', {
        community_id: community.community_id,
        user_id: member.user_id,
      })
      await loadData(community.community_id)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Member could not be removed.')
    } finally {
      setActionBusy('')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading collaborators..." />
  if (pageError) return <ErrorState title="Collaborators unavailable" message={pageError} />

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
              <h1 className="mt-1 text-2xl font-semibold text-foreground">Collaborators</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Invite collaborators, adjust roles, and manage the member roster for a
                selected app community.
              </p>
              {community && (
                <div className="mt-3 flex items-center gap-2">
                  <StatusPill tone={memberStatusTone(community.status)}>
                    {humanizeLabel(community.status)}
                  </StatusPill>
                  <span className="text-sm text-muted-foreground">{community.name}</span>
                </div>
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
                <Button asChild>
                  <Link to={buildCommunitySubPageUrl(appId, 'governance', selectedCommunityId)}>
                    Governance
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
              title="Collaborator management"
              subtitle="Owner and admin members can invite, re-role, and remove collaborators here."
            >
              <SummaryStrip items={summaryItems} />
            </Panel>

            {/* New invitation token */}
            {latestInvitation?.invite_token && (
              <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
                <div className="flex flex-col gap-2">
                  <div className="text-sm font-semibold text-foreground">
                    Invitation created
                  </div>
                  <div className="text-sm text-muted-foreground">
                    Share this one-time invite token through a secure channel.
                  </div>
                  <code className="rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground">
                    {latestInvitation.invite_token}
                  </code>
                </div>
              </div>
            )}

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              {/* Invite form */}
              <Panel
                title="Invite collaborator"
                subtitle="Create a direct or email-based invitation for this community."
              >
                {canEdit ? (
                  <form className="grid gap-4" onSubmit={handleCreateInvitation}>
                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Invite email</span>
                      <input
                        type="email"
                        value={inviteEmail}
                        onChange={(e) => setInviteEmail(e.target.value)}
                        className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                        placeholder="teammate@example.com"
                      />
                    </label>

                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Invitee user id</span>
                      <input
                        type="text"
                        value={inviteUserId}
                        onChange={(e) => setInviteUserId(e.target.value)}
                        className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                        placeholder="user-123"
                      />
                    </label>

                    <label className="grid gap-1 text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">Role</span>
                      <select
                        value={inviteRole}
                        onChange={(e) => setInviteRole(e.target.value)}
                        className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
                      >
                        {MEMBER_ROLES.map((role) => (
                          <option key={role} value={role}>
                            {humanizeLabel(role)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <div>
                      <Button type="submit" disabled={actionBusy === 'invite'}>
                        {actionBusy === 'invite' ? 'Creating...' : 'Create invitation'}
                      </Button>
                    </div>
                  </form>
                ) : (
                  <InlineEmptyState
                    title="Read-only collaborator view"
                    description="Only owner and admin community roles can create invitations or manage roster changes."
                  />
                )}
              </Panel>

              {/* Pending invitations */}
              <Panel
                title="Pending invitations"
                subtitle="Track pending, accepted, declined, or expired invitations."
              >
                <ResourceList
                  items={invitations}
                  columns={invitationColumns}
                  getItemId={(item, idx) => item.invitation_id ?? `invitation-${idx}`}
                  empty={{
                    title: 'No invitations yet',
                    description:
                      'Create the first collaborator invitation for this community.',
                  }}
                />
              </Panel>
            </div>

            {/* Member roster */}
            <Panel
              title="Member roster"
              subtitle="Update collaborator roles or remove access from the current community roster."
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
                    'Members appear here after invitations are accepted or members are added directly.',
                }}
              />
            </Panel>
          </>
        ) : (
          /* ---- No community state ---- */
          <Panel
            title="No community selected"
            subtitle="Create a community before managing collaborators."
          >
            <InlineEmptyState
              title="Create a community first"
              description="Collaborator management becomes available after the app has a hosted community."
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
