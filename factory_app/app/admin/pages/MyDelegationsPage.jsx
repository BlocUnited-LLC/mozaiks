/**
 * MyDelegationsPage — User's voting delegations.
 *
 * Shows outgoing delegations (granted by the user) and incoming delegations
 * (granted to the user). Outgoing active delegations can be revoked.
 * Delegation is non-transitive and only affects future proposal snapshots.
 *
 * Route: /delegations
 */

import { useState, useEffect, useMemo } from 'react'
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
  delegationStatusTone,
  callModuleAction,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MyDelegationsPage() {
  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [delegations, setDelegations] = useState([])
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  async function loadData() {
    setLoading(true)
    setPageError(null)
    try {
      const result = await callModuleAction('community_governance', 'list_delegations', {
        limit: 200,
      })
      setDelegations(result.delegations || [])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Delegations could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [currentUserId])

  async function handleRevoke(delegation) {
    setActionBusy(`revoke:${delegation.delegation_id}`)
    setActionError(null)
    try {
      await callModuleAction('community_governance', 'revoke_delegation', {
        community_id: delegation.community_id,
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

  // Delegations the current user has granted to others
  const outgoingDelegations = useMemo(
    () => delegations.filter((d) => d.delegator_user_id === currentUserId),
    [delegations, currentUserId],
  )

  // Delegations others have granted to the current user
  const incomingDelegations = useMemo(
    () => delegations.filter((d) => d.delegate_user_id === currentUserId),
    [delegations, currentUserId],
  )

  const outgoingColumns = [
    {
      id: 'community',
      header: 'Community',
      render: (d) => (
        <div>
          <div className="font-semibold text-foreground">
            {d.community_name || d.community_id}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            Created {formatShortDate(d.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'delegate',
      header: 'Delegated to',
      render: (d) => (
        <span className="text-sm text-foreground">{d.delegate_user_id}</span>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (d) => (
        <StatusPill tone={delegationStatusTone(d.status)}>
          {humanizeLabel(d.status)}
        </StatusPill>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      width: '12rem',
      cellClassName: 'text-right',
      render: (d) =>
        d.status === 'active' ? (
          <Button
            variant="outline"
            disabled={actionBusy === `revoke:${d.delegation_id}`}
            onClick={() => handleRevoke(d)}
          >
            {actionBusy === `revoke:${d.delegation_id}` ? 'Revoking…' : 'Revoke'}
          </Button>
        ) : null,
    },
  ]

  const incomingColumns = [
    {
      id: 'community',
      header: 'Community',
      render: (d) => (
        <div>
          <div className="font-semibold text-foreground">
            {d.community_name || d.community_id}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            Created {formatShortDate(d.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'delegator',
      header: 'Delegated by',
      render: (d) => (
        <span className="text-sm text-foreground">{d.delegator_user_id}</span>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (d) => (
        <StatusPill tone={delegationStatusTone(d.status)}>
          {humanizeLabel(d.status)}
        </StatusPill>
      ),
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading delegations..." />
  if (pageError) return <ErrorState title="Delegations unavailable" message={pageError} />

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
              <h1 className="mt-1 text-2xl font-semibold text-foreground">My Delegations</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Delegations you have granted and delegations others have granted to you.
                Delegation is non-transitive and affects future proposal snapshots only.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link to="/communities">My Communities</Link>
              </Button>
            </div>
          </div>
        </div>

        {/* ---- Action error ---- */}
        {actionError && (
          <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
            <div className="text-sm text-destructive">{actionError}</div>
          </div>
        )}

        {/* ---- Non-transitive notice ---- */}
        <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
          <div className="text-sm text-muted-foreground">
            Delegation changes affect future proposal snapshots only. Revoking a delegation
            does not change past snapshot weights.
          </div>
        </div>

        {/* ---- Outgoing delegations ---- */}
        <Panel
          title="Delegations you granted"
          subtitle="Communities where you have delegated your future voting power to another member."
        >
          <ResourceList
            items={outgoingDelegations}
            columns={outgoingColumns}
            getItemId={(item, idx) => `out:${item.delegation_id ?? idx}`}
            empty={{
              title: 'No outgoing delegations',
              description: 'You have not delegated your voting power in any community.',
            }}
          />
        </Panel>

        {/* ---- Incoming delegations ---- */}
        <Panel
          title="Delegations granted to you"
          subtitle="Members who have delegated their future voting power to you."
        >
          <ResourceList
            items={incomingDelegations}
            columns={incomingColumns}
            getItemId={(item, idx) => `in:${item.delegation_id ?? idx}`}
            empty={{
              title: 'No incoming delegations',
              description:
                'No community members have delegated their voting power to you.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
