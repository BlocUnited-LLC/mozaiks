/**
 * MyInvitationsPage — Pending community invitations for the current user.
 *
 * Users paste the invite token from the invitation message and can accept or
 * decline. The token is required for both operations.
 *
 * Route: /invitations
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
  invitationStatusTone,
  callModuleAction,
} from './communityStudioShared.js'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MyInvitationsPage() {
  const { user } = useChatUI()
  const currentUserId = getUserId(user)

  const [invitations, setInvitations] = useState([])
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [actionBusy, setActionBusy] = useState('')

  // Per-invitation token input values, keyed by invitation_id
  const [tokenValues, setTokenValues] = useState({})

  async function loadData() {
    setLoading(true)
    setPageError(null)
    try {
      const result = await callModuleAction('community_membership', 'list_invitations', {
        limit: 200,
      })
      setInvitations(result.invitations || [])
    } catch (err) {
      setPageError(err instanceof Error ? err.message : 'Invitations could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [currentUserId])

  const pendingInvitations = useMemo(
    () => invitations.filter((inv) => inv.status === 'pending'),
    [invitations],
  )

  function setTokenForInvitation(invitationId, value) {
    setTokenValues((prev) => ({ ...prev, [invitationId]: value }))
  }

  async function handleAccept(invitation) {
    const token = (tokenValues[invitation.invitation_id] || '').trim()
    if (!token) {
      setActionError(
        'Paste the invite token from the invitation message before accepting.',
      )
      return
    }
    setActionBusy(`accept:${invitation.invitation_id}`)
    setActionError(null)
    try {
      await callModuleAction('community_membership', 'accept_invitation', {
        invitation_id: invitation.invitation_id,
        token,
      })
      // Clear the token for this invitation
      setTokenValues((prev) => {
        const next = { ...prev }
        delete next[invitation.invitation_id]
        return next
      })
      await loadData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Invitation could not be accepted.')
    } finally {
      setActionBusy('')
    }
  }

  async function handleDecline(invitation) {
    const token = (tokenValues[invitation.invitation_id] || '').trim()
    if (!token) {
      setActionError(
        'Paste the invite token from the invitation message before declining.',
      )
      return
    }
    setActionBusy(`decline:${invitation.invitation_id}`)
    setActionError(null)
    try {
      await callModuleAction('community_membership', 'decline_invitation', {
        invitation_id: invitation.invitation_id,
        token,
      })
      setTokenValues((prev) => {
        const next = { ...prev }
        delete next[invitation.invitation_id]
        return next
      })
      await loadData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Invitation could not be declined.')
    } finally {
      setActionBusy('')
    }
  }

  const invitationColumns = [
    {
      id: 'community',
      header: 'Community',
      render: (invitation) => (
        <div>
          <div className="font-semibold text-foreground">
            {invitation.community_name || invitation.community_id}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            {humanizeLabel(invitation.role)} · Sent {formatShortDate(invitation.created_at)}
          </div>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '10rem',
      render: (invitation) => (
        <StatusPill tone={invitationStatusTone(invitation.status)}>
          {humanizeLabel(invitation.status)}
        </StatusPill>
      ),
    },
    {
      id: 'respond',
      header: 'Respond',
      render: (invitation) =>
        invitation.status === 'pending' ? (
          <div className="grid gap-2">
            <input
              type="text"
              value={tokenValues[invitation.invitation_id] || ''}
              onChange={(e) => setTokenForInvitation(invitation.invitation_id, e.target.value)}
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
              placeholder="Paste invite token…"
            />
            <div className="flex gap-2">
              <Button
                disabled={actionBusy === `accept:${invitation.invitation_id}`}
                onClick={() => handleAccept(invitation)}
              >
                {actionBusy === `accept:${invitation.invitation_id}` ? 'Accepting…' : 'Accept'}
              </Button>
              <Button
                variant="outline"
                disabled={actionBusy === `decline:${invitation.invitation_id}`}
                onClick={() => handleDecline(invitation)}
              >
                {actionBusy === `decline:${invitation.invitation_id}` ? 'Declining…' : 'Decline'}
              </Button>
            </div>
          </div>
        ) : null,
    },
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) return <LoadingState label="Loading invitations..." />
  if (pageError) return <ErrorState title="Invitations unavailable" message={pageError} />

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
              <h1 className="mt-1 text-2xl font-semibold text-foreground">My Invitations</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Community invitations sent to you. Paste the token from the invitation
                message to accept or decline.
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

        {/* ---- Pending count notice ---- */}
        {pendingInvitations.length > 0 && (
          <div className="rounded-2xl border border-border/42 bg-card/30 px-6 py-4">
            <div className="text-sm font-semibold text-foreground">
              {pendingInvitations.length} pending invitation
              {pendingInvitations.length !== 1 ? 's' : ''}
            </div>
            <div className="mt-1 text-sm text-muted-foreground">
              Accepting an invitation adds you to the community roster for future proposal
              snapshots.
            </div>
          </div>
        )}

        {/* ---- Invitations list ---- */}
        <Panel title="Invitations" subtitle="All invitations sent to your account.">
          <ResourceList
            items={invitations}
            columns={invitationColumns}
            getItemId={(item, idx) => item.invitation_id ?? `invitation-${idx}`}
            empty={{
              title: 'No invitations',
              description: 'You have no pending or past community invitations.',
            }}
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
