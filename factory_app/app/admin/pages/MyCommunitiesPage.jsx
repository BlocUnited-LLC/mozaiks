/**
 * MyCommunitiesPage — User's community memberships across all apps.
 *
 * Shows all communities the current user belongs to, with a link to each
 * app's community Studio.
 *
 * Route: /communities
 */

import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ErrorState,
  LoadingState,
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
  callModuleAction,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MyCommunitiesPage() {
  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [communities, setCommunities] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const result = await callModuleAction('community_membership', 'list_communities', {
        limit: 200,
      })
      setCommunities(result.communities || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Communities could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [currentUserId])

  const activeCommunities = communities.filter((c) => c.status === 'active')

  const summaryItems = [
    {
      id: 'total',
      label: 'Communities',
      value: String(communities.length),
      detail: 'All memberships',
    },
    {
      id: 'active',
      label: 'Active',
      value: String(activeCommunities.length),
      detail: 'Active memberships',
    },
    {
      id: 'other',
      label: 'Other',
      value: String(communities.length - activeCommunities.length),
      detail: 'Pending or archived',
    },
  ]

  const communityColumns = [
    {
      id: 'name',
      header: 'Community',
      render: (community) => (
        <div>
          <div className="font-semibold text-foreground">{community.name}</div>
          <div className="mt-1 text-sm text-muted-foreground">
            Created {formatShortDate(community.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (community) => (
        <StatusPill tone={memberStatusTone(community.status)}>
          {humanizeLabel(community.status)}
        </StatusPill>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      width: '12rem',
      cellClassName: 'text-right',
      render: (community) =>
        community.app_id ? (
          <Link
            to={`/apps/${community.app_id}/collaborators?community=${encodeURIComponent(community.community_id)}`}
            className="text-sm text-primary hover:underline"
          >
            Open Studio
          </Link>
        ) : null,
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading communities..." />
  if (error) return <ErrorState title="Communities unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        {/* ---- Page header ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-5">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Account
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">My Communities</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Communities you belong to, across all apps.
          </p>
        </div>

        {/* ---- Summary ---- */}
        <Panel
          title="Membership summary"
          subtitle="Your community memberships across all apps."
        >
          <SummaryStrip items={summaryItems} />
        </Panel>

        {/* ---- Community list ---- */}
        <Panel title="Communities" subtitle="All communities you are a member of.">
          <ResourceList
            items={communities}
            columns={communityColumns}
            getItemId={(item, idx) => item.community_id ?? `community-${idx}`}
            empty={{
              title: 'No communities yet',
              description:
                'You will appear here after accepting an invitation to a community.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
