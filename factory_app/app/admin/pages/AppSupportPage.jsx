import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { ChatThread } from '@mozaiks/chat-ui/ui'
import {
  Panel,
  StatusPill,
  StudioErrorState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function fetchCurrentProfileAppId() {
  try {
    const response = await fetch('/api/me')
    if (!response.ok) return null
    const profile = await response.json()
    return profile?.app_id || profile?.appId || null
  } catch (_) {
    return null
  }
}

function supportStatusForRun(run) {
  if (run.support_status === 'resolved' || run.status === 'resolved' || run.status === 2) {
    return { label: 'Resolved', tone: 'success', bucket: 'resolved' }
  }
  if (run.last_message_by_role === 'operator' || run.support_status === 'responded') {
    return { label: 'Responded', tone: 'primary', bucket: 'responded' }
  }
  if (
    run.support_status === 'needs-reply' ||
    run.last_message_by_role === 'user' ||
    run.last_message_by_role === 'assistant' ||
    run.awaiting_operator ||
    Number(run.errors || 0) > 0 ||
    run.status === 0 ||
    (!run.ended_at && Number(run.agent_turns || 0) > 30)
  ) {
    return { label: 'Needs reply', tone: 'warning', bucket: 'needs-reply' }
  }
  if (run.ended_at) {
    return { label: 'Resolved', tone: 'success', bucket: 'resolved' }
  }
  return { label: 'In progress', tone: 'primary', bucket: 'in-progress' }
}

const SUPPORT_FILTERS = [
  { id: 'needs-reply', label: 'Needs reply' },
  { id: 'responded', label: 'Responded' },
  { id: 'resolved', label: 'Resolved' },
  { id: 'all', label: 'All' },
]

function relativeTime(iso) {
  if (!iso) return null
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function userInitials(userId) {
  if (!userId) return '?'
  const clean = userId.replace(/@.*$/, '').replace(/[._-]+/g, ' ').trim()
  const words = clean.split(/\s+/).filter(Boolean)
  if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase()
  return (words[0] || '?').slice(0, 2).toUpperCase()
}

// ─── Avatar ───────────────────────────────────────────────────────────────────

function UserAvatar({ userId, size = 'md' }) {
  const initials = userInitials(userId)
  const sz = size === 'sm' ? 'h-7 w-7 text-[10px]' : 'h-9 w-9 text-xs'
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full bg-primary/80 font-bold text-primary-foreground shadow-sm ring-1 ring-primary/25 ${sz}`}
      aria-hidden="true"
    >
      {initials}
    </span>
  )
}

// ─── Session list card ────────────────────────────────────────────────────────

function SessionListCard({ run, active, onClick }) {
  const supportStatus = supportStatusForRun(run)
  const needsAttention = supportStatus.bucket === 'needs-reply'
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'w-full text-left rounded-2xl border px-3 py-3 transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-primary/50',
        active
          ? 'border-primary/70 bg-primary/15 shadow-[0_10px_35px_rgba(6,182,212,0.15)]'
          : needsAttention
            ? 'border-destructive/25 bg-card/65 hover:border-destructive/45 hover:bg-card/80'
            : 'border-border/20 bg-card/65 hover:border-border/40 hover:bg-card/80',
      ].join(' ')}
    >
      <div className="flex items-start gap-2.5">
        <UserAvatar userId={run.user_id} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <span className="truncate text-xs font-semibold text-foreground/90">{run.subject || run.workflow_name || 'Support request'}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground/50">{relativeTime(run.started_at)}</span>
          </div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground/65">
            {run.user_id || 'Unknown user'} · {run.app_id || 'workspace'}
          </div>
          <div className="mt-1.5">
            <StatusPill tone={supportStatus.tone}>{supportStatus.label}</StatusPill>
          </div>
        </div>
      </div>
    </button>
  )
}

// ─── Thread detail panel ──────────────────────────────────────────────────────

function ThreadPanel({ run, appId, onMessageSent, onDeleted, onStatusUpdated }) {
  const [extraMessages, setExtraMessages] = useState([])
  const [sendError, setSendError] = useState(null)
  const [deleteError, setDeleteError] = useState(null)
  const [statusError, setStatusError] = useState(null)
  const [statusUpdating, setStatusUpdating] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => { setExtraMessages([]) }, [run?.chat_id])

  if (!run) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/30 bg-card/40 px-6 py-12 text-center">
        <p className="text-sm font-medium text-foreground">No support chat selected</p>
        <p className="text-xs text-muted-foreground/60">Select a request to review its conversation.</p>
      </div>
    )
  }

  const messages = [...(run.messages || []), ...extraMessages]
  const supportStatus = supportStatusForRun(run)

  async function handleSend(text) {
    setSendError(null)
    if (run.request_id) {
      try {
        const response = await fetch('/api/modules/workspace_support/add_support_message', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_id: run.request_id, message: text, sender_role: 'operator', app_id: appId }),
        })
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
        const body = await response.json()
        if (!body?.success) throw new Error(body?.error || 'Reply could not be sent.')
        await onMessageSent?.()
      } catch (err) {
        setSendError(err?.message || 'Reply could not be sent.')
      }
    } else {
      setExtraMessages((prev) => [...prev, { role: 'operator', content: text, senderLabel: 'Support' }])
    }
  }

  async function handleStatusChange(nextStatus) {
    if (!run?.request_id || statusUpdating) return
    setStatusUpdating(true)
    setStatusError(null)
    try {
      const response = await fetch('/api/modules/workspace_support/update_support_request_status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: run.request_id, status: nextStatus, app_id: appId }),
      })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      const body = await response.json()
      if (!body?.success) throw new Error(body?.error || 'Status was not updated.')
      await onStatusUpdated?.()
    } catch (err) {
      setStatusError(err?.message || 'Support request status could not be updated.')
    } finally {
      setStatusUpdating(false)
    }
  }

  async function handleDelete() {
    if (!run?.request_id || deleting) return
    const confirmed = window.confirm(`Delete support request "${run.subject || run.request_id}"? This removes the linked thread and messages.`)
    if (!confirmed) return
    setDeleting(true)
    setDeleteError(null)
    try {
      const response = await fetch('/api/modules/workspace_support/delete_support_request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: run.request_id, app_id: appId }),
      })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      const body = await response.json()
      if (!body?.success) throw new Error(body?.error || 'Request was not deleted.')
      await onDeleted?.(run.request_id)
    } catch (err) {
      setDeleteError(err?.message || 'Support request could not be deleted.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex flex-1 min-w-0 flex-col overflow-hidden rounded-2xl border border-border/20 bg-card/50">

      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border/20 px-4 py-3">
        <div className="flex items-center gap-2 min-w-0">
          <UserAvatar userId={run.user_id} size="sm" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">{run.user_id || 'Unknown user'}</div>
            <div className="text-[11px] text-muted-foreground/60">{run.subject || run.workflow_name} · {relativeTime(run.started_at)}</div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <StatusPill tone={supportStatus.tone}>{supportStatus.label}</StatusPill>
          {run.request_id && (
            <>
              <button
                type="button"
                onClick={() => handleStatusChange(run.status === 'resolved' ? 'open' : 'resolved')}
                disabled={statusUpdating}
                className="rounded-lg border border-border/40 bg-card/70 px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:opacity-50"
              >
                {statusUpdating ? 'Saving…' : run.status === 'resolved' ? 'Reopen' : 'Resolve'}
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-1 text-[11px] font-semibold text-destructive transition-colors hover:border-destructive/60 hover:bg-destructive/15 disabled:opacity-50"
                title="Delete support request"
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* ChatThread primitive handles messages + input */}
      <ChatThread
        messages={messages}
        onSend={run.status === 'resolved' ? undefined : handleSend}
        inputPlaceholder="Reply to this ticket…"
        emptyText="No messages yet."
        className="flex-1 min-h-0"
      />
      {sendError && (
        <div className="border-t border-destructive/20 px-4 py-2 text-xs text-destructive">
          {sendError}
        </div>
      )}
      {deleteError && (
        <div className="border-t border-destructive/20 px-4 py-2 text-xs text-destructive">
          {deleteError}
        </div>
      )}
      {statusError && (
        <div className="border-t border-destructive/20 px-4 py-2 text-xs text-destructive">
          {statusError}
        </div>
      )}

    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

// Convert a workspace_support request record to the session shape AppSupportPage expects.
function supportRequestToRun(req) {
  const rid = req.request_id || req.id
  const subject = req.subject || req.page_title || String(req.message || 'Support request').slice(0, 80)
  const appId = req.subject_app_id || req.app_id || 'workspace'
  const messages = Array.isArray(req.messages) && req.messages.length > 0
    ? req.messages
    : req.message
      ? [{ role: 'user', content: req.message }]
      : []
  const lastMessageByRole = req.last_message_by_role || messages[messages.length - 1]?.role || 'user'
  const isResolved = req.status === 'resolved'
  return {
    chat_id: rid || String(Math.random()),
    request_id: rid || null,   // kept for add_support_message POSTs
    workflow_name: subject,
    subject,
    app_id: appId,
    user_id: req.user_id || req.submitted_by || 'user',
    agent_turns: 0,
    tool_calls: 0,
    errors: 0,
    started_at: req.created_at || new Date().toISOString(),
    ended_at: isResolved ? req.resolved_at || req.updated_at || req.created_at : null,
    status: req.status || 'open',
    support_status: isResolved ? 'resolved' : lastMessageByRole === 'operator' ? 'responded' : 'needs-reply',
    awaiting_operator: !isResolved && lastMessageByRole !== 'operator',
    last_message_by_role: lastMessageByRole,
    messages,
  }
}

export default function AppSupportPage() {
  const { appId = 'workspace-app' } = useParams()
  const [effectiveAppId, setEffectiveAppId] = useState(appId)
  const [selectedId, setSelectedId] = useState(null)
  const [activeFilter, setActiveFilter] = useState('needs-reply')
  const [supportRequests, setSupportRequests] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const loadSupportRequests = useCallback(async (options = {}) => {
    const silent = Boolean(options?.silent)
    if (!silent) setLoading(true)
    setError(null)
    try {
      let targetAppId = appId
      if (!targetAppId || targetAppId === 'default' || targetAppId === 'workspace-app') {
        targetAppId = await fetchCurrentProfileAppId() || targetAppId
      }
      setEffectiveAppId(targetAppId)
      const res = await fetch('/api/modules/workspace_support/list_support_requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'all', limit: 50, scope: 'app', app_id: targetAppId }),
      })
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`)
      }
      const body = await res.json()
      const nextRuns = (body?.requests || []).map(supportRequestToRun)
      setSupportRequests(nextRuns)
      setSelectedId((current) => (
        current && nextRuns.some((run) => run.chat_id === current)
          ? current
          : nextRuns[0]?.chat_id || null
      ))
    } catch (err) {
      setSupportRequests([])
      setError(err?.message || 'Support chats could not be loaded.')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [appId])

  useEffect(() => { loadSupportRequests() }, [loadSupportRequests])

  useEffect(() => {
    const refresh = () => loadSupportRequests({ silent: true })
    const intervalId = window.setInterval(refresh, 5000)
    const handleFocus = () => refresh()
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    window.addEventListener('focus', handleFocus)
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      window.clearInterval(intervalId)
      window.removeEventListener('focus', handleFocus)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [loadSupportRequests])

  if (loading) return <StudioLoadingState label="Loading support chats…" />
  if (error) return <StudioErrorState title="Support Unavailable" message={error} />

  const allRuns = supportRequests
  const runs = allRuns.filter((run) => {
    if (activeFilter === 'all') return true
    return supportStatusForRun(run).bucket === activeFilter
  })
  const selectedRun = runs.find((r) => r.chat_id === selectedId) || null

  const statusBuckets = allRuns.reduce((counts, run) => {
    const bucket = supportStatusForRun(run).bucket
    counts[bucket] = (counts[bucket] || 0) + 1
    return counts
  }, {})
  const needsReplyCount = statusBuckets['needs-reply'] || 0
  const respondedCount = statusBuckets.responded || 0
  const resolvedCount = statusBuckets.resolved || 0

  const summaryItems = [
    { id: 'chats', label: 'Support chats', value: formatCompactNumber(allRuns.length, '0'), detail: 'For this app' },
    { id: 'needs-reply', label: 'Needs reply', value: formatCompactNumber(needsReplyCount, '0'), detail: 'Waiting on support' },
    { id: 'responded', label: 'Responded', value: formatCompactNumber(respondedCount, '0'), detail: 'Waiting on user' },
    { id: 'resolved', label: 'Resolved', value: formatCompactNumber(resolvedCount, '0'), detail: 'Closed chats' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Support"
          subtitle={`Review support chats for ${effectiveAppId}, respond, resolve, or remove requests.`}
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Inbox"
          title="Support chats"
          subtitle="Click a chat to review the conversation, reply, resolve, or remove it."
          action={needsReplyCount > 0
            ? <StatusPill tone="warning">{needsReplyCount} need reply</StatusPill>
            : <StatusPill tone="success">No open replies</StatusPill>}
        >
          <div className="mb-4 flex flex-wrap gap-2">
            {SUPPORT_FILTERS.map((filter) => {
              const count = filter.id === 'all' ? allRuns.length : statusBuckets[filter.id] || 0
              const active = activeFilter === filter.id
              return (
                <button
                  key={filter.id}
                  type="button"
                  onClick={() => setActiveFilter(filter.id)}
                  className={[
                    'rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors',
                    active
                      ? 'border-primary/60 bg-primary/15 text-foreground'
                      : 'border-border/30 bg-card/50 text-muted-foreground hover:border-border/60 hover:text-foreground',
                  ].join(' ')}
                >
                  {filter.label} <span className="ml-1 text-muted-foreground/70">{count}</span>
                </button>
              )
            })}
          </div>

          {/* Two-pane inbox */}
          <div className="flex gap-3" style={{ minHeight: '480px' }}>

            {/* Left: session list */}
            <div className="flex w-64 shrink-0 flex-col gap-1.5 overflow-y-auto xl:w-72">
              {runs.length > 0 ? runs.map((run) => (
                <SessionListCard
                  key={run.chat_id}
                  run={run}
                  active={run.chat_id === selectedId}
                  onClick={() => setSelectedId(run.chat_id === selectedId ? null : run.chat_id)}
                />
              )) : (
                <div className="rounded-2xl border border-dashed border-border/30 bg-card/40 px-4 py-8 text-center text-sm text-muted-foreground/70">
                  No support chats yet.
                </div>
              )}
            </div>

            {/* Right: thread detail */}
            <ThreadPanel
              run={selectedRun}
              appId={effectiveAppId}
              onMessageSent={loadSupportRequests}
              onStatusUpdated={loadSupportRequests}
              onDeleted={async () => {
                setSelectedId(null)
                await loadSupportRequests()
              }}
            />
          </div>
        </Panel>

      </div>
    </WorkspaceLayout>
  )
}
