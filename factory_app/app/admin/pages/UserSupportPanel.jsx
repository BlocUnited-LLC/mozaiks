/**
 * UserSupportPanel — profile panel component for workspace_support.
 *
 * Rendered by ProfilePage when the workspace_support module declares
 * contracts/profile.yaml with kind: component, component: UserSupportPanel.
 *
 * Props (from panel contract runtime):
 *   panel  — the panel manifest (id, title, description, order)
 *   data   — result of list_support_requests action, shape: { requests: [], total: int }
 *
 * Tickets are grouped by app_id so users with tickets across multiple apps
 * see them organised clearly.
 *
 */

import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { ChatThread } from '@mozaiks/chat-ui/ui'

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SUPPORT_PANEL_LOG_PREFIX = '[mozaiks-support-panel]'

function supportPanelTrace(event, details = {}) {
  try {
    console.info(SUPPORT_PANEL_LOG_PREFIX, event, details)
  } catch (_) {}
}

function supportPanelWarn(event, details = {}) {
  try {
    console.warn(SUPPORT_PANEL_LOG_PREFIX, event, details)
  } catch (_) {}
}

function formatRelative(iso) {
  if (!iso) return '—'
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  } catch { return '—' }
}

function normaliseRequest(r) {
  const fallbackMessage = String(r.message || '').trim()
  return {
    id:        r.request_id || r.id,
    ticketId:  r.ticket_id || r.ticketId || r.request_id || r.id,
    appId:     r.app_id || 'platform',
    appLabel:  r.app_label || r.app_name || r.app_id || 'Platform',
    userId:    r.user_id || r.userId || r.submitted_by || null,
    subject:   r.subject || r.page_title || r.message?.slice(0, 60) || 'Support request',
    status:    r.status || 'open',
    updatedAt: r.updated_at || r.updatedAt || r.created_at,
    messages:  Array.isArray(r.messages) ? r.messages : (fallbackMessage ? [{ role: 'user', content: fallbackMessage }] : []),
  }
}

async function postMessage({ requestId, appId, userId, message }) {
  try {
    supportPanelTrace('message:add:start', {
      requestId,
      appId,
      userId,
      messageLength: String(message || '').length,
    })
    const response = await fetch('/api/modules/workspace_support/add_support_message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: requestId,
        message,
        sender_role: 'user',
        app_id: appId,
        user_id: userId,
      }),
    })
    if (!response.ok) {
      supportPanelWarn('message:add:failed_http', {
        requestId,
        appId,
        userId,
        status: response.status,
      })
      return null
    }
    const body = await response.json()
    supportPanelTrace('message:add:success', {
      requestId,
      messageId: body?.message_id || null,
      messageThreadId: body?.message_thread_id || null,
      success: body?.success,
    })
    return body
  } catch (error) {
    supportPanelWarn('message:add:failed_exception', {
      requestId,
      appId,
      userId,
      error: error?.message || String(error || ''),
    })
    return null
  }
}

async function updateRequestStatus({ requestId, appId, status }) {
  const response = await fetch('/api/modules/workspace_support/update_support_request_status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId, app_id: appId, status }),
  })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  const body = await response.json()
  if (!body?.success) throw new Error(body?.error || 'Status was not updated.')
  return body
}

async function deleteRequest({ requestId, appId }) {
  const response = await fetch('/api/modules/workspace_support/delete_support_request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId, app_id: appId }),
  })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  const body = await response.json()
  if (!body?.success) throw new Error(body?.error || 'Request was not removed.')
  return body
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function AppGroupHeader({ appLabel, count, open, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-muted/10 transition-colors"
    >
      <span className={`text-[9px] transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
      <span className="min-w-0 flex-1 text-left text-xs font-semibold text-foreground truncate">{appLabel}</span>
      <span className="shrink-0 rounded-full bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
        {count}
      </span>
    </button>
  )
}

function TicketRow({ req, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left flex items-start gap-3 pl-8 pr-4 py-3 transition-colors hover:bg-muted/20 border-t border-border/10 ${active ? 'bg-muted/40' : ''}`}
    >
      <span className={`shrink-0 h-2 w-2 rounded-full mt-1.5 ${req.status === 'resolved' ? 'bg-success' : 'bg-warning'}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <span className="text-xs font-semibold text-foreground truncate">{req.subject}</span>
          <span className="shrink-0 text-[10px] text-muted-foreground/50">{formatRelative(req.updatedAt)}</span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className="text-[10px] font-mono text-muted-foreground/50">{req.ticketId}</span>
          <span className={`shrink-0 rounded-sm px-1 py-px text-[9px] font-semibold uppercase tracking-wider ${
            req.status === 'resolved' ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning/80'
          }`}>{req.status}</span>
        </div>
      </div>
    </button>
  )
}

// ─── Root component ───────────────────────────────────────────────────────────

export default function UserSupportPanel({ panel, data, onNewSupport }) {
  const location = useLocation()
  const queryRequestId = new URLSearchParams(location.search || '').get('request_id') || null
  const rawRequests = data?.requests?.length > 0 ? data.requests : []
  const [statusOverrides, setStatusOverrides] = useState({})
  const [hiddenRequestIds, setHiddenRequestIds] = useState({})
  const [extraMessages, setExtraMessages] = useState({})
  const [actionError, setActionError] = useState(null)
  const [busyRequestId, setBusyRequestId] = useState(null)
  const requests = rawRequests
    .map(normaliseRequest)
    .filter((req) => !hiddenRequestIds[req.id])
    .map((req) => ({ ...req, status: statusOverrides[req.id] || req.status }))

  // Group by appId, preserving insertion order
  const groups = []
  const groupMap = {}
  for (const req of requests) {
    if (!groupMap[req.appId]) {
      const g = { appId: req.appId, appLabel: req.appLabel, tickets: [] }
      groups.push(g)
      groupMap[req.appId] = g
    }
    groupMap[req.appId].tickets.push(req)
  }

  const firstTicketId = groups[0]?.tickets[0]?.id || null
  const [selectedId, setSelectedId] = useState(queryRequestId || firstTicketId)
  const [openGroups, setOpenGroups] = useState(() => Object.fromEntries(groups.map(g => [g.appId, true])))

  const allTickets = groups.flatMap(g => g.tickets)
  const ticketIdsKey = allTickets.map(req => req.id).join('|')
  const groupIdsKey = groups.map(g => g.appId).join('|')
  const selected = allTickets.find(r => r.id === selectedId) || allTickets[0] || null

  useEffect(() => {
    supportPanelTrace('data:received', {
      queryRequestId,
      rawRequestCount: rawRequests.length,
      normalisedRequestCount: requests.length,
      groupCount: groups.length,
      requestIds: requests.map(req => req.id).slice(0, 10),
      messageThreadIds: rawRequests.map(req => req.message_thread_id || null).slice(0, 10),
      selectedId,
      selectedExists: Boolean(selected),
      selectedMessageCount: selected?.messages?.length || 0,
      panelId: panel?.id || null,
      panelError: panel?.error || null,
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryRequestId, rawRequests.length, ticketIdsKey, selectedId])

  useEffect(() => {
    setOpenGroups(prev => {
      const next = { ...prev }
      for (const group of groups) {
        if (typeof next[group.appId] === 'undefined') next[group.appId] = true
      }
      return next
    })
  }, [groupIdsKey])

  useEffect(() => {
    const hasQueryTicket = queryRequestId && allTickets.some(req => req.id === queryRequestId)
    if (hasQueryTicket) {
      supportPanelTrace('selection:query_request_matched', {
        queryRequestId,
      })
      setSelectedId(queryRequestId)
      return
    }
    const hasSelectedTicket = selectedId && allTickets.some(req => req.id === selectedId)
    if (!hasSelectedTicket) {
      supportPanelWarn('selection:request_missing', {
        queryRequestId,
        previousSelectedId: selectedId,
        fallbackTicketId: firstTicketId || null,
        availableRequestIds: allTickets.map(req => req.id).slice(0, 10),
      })
      setSelectedId(firstTicketId || null)
    }
  }, [queryRequestId, firstTicketId, selectedId, ticketIdsKey])

  function toggleGroup(appId) {
    setOpenGroups(prev => ({ ...prev, [appId]: !prev[appId] }))
  }

  async function handleSend(text) {
    if (!selected) return
    setActionError(null)
    supportPanelTrace('thread:send_start', {
      requestId: selected.id,
      ticketId: selected.ticketId,
      appId: selected.appId,
      userId: selected.userId,
      existingMessageCount: threadMessages.length,
      messageLength: String(text || '').length,
    })
    const body = await postMessage({
      requestId: selected.id,
      appId: selected.appId,
      userId: selected.userId,
      message: text,
    })
    if (body?.success) {
      setExtraMessages(prev => ({
        ...prev,
        [selected.id]: [...(prev[selected.id] || []), { role: 'user', content: text }],
      }))
    }
  }

  async function handleStatusChange(nextStatus) {
    if (!selected || busyRequestId) return
    setBusyRequestId(selected.id)
    setActionError(null)
    try {
      await updateRequestStatus({
        requestId: selected.id,
        appId: selected.appId,
        status: nextStatus,
      })
      setStatusOverrides(prev => ({ ...prev, [selected.id]: nextStatus }))
    } catch (error) {
      setActionError(error?.message || 'Status could not be updated.')
    } finally {
      setBusyRequestId(null)
    }
  }

  async function handleDelete() {
    if (!selected || busyRequestId) return
    const confirmed = window.confirm(`Remove support request "${selected.subject}"? This removes the linked conversation.`)
    if (!confirmed) return
    setBusyRequestId(selected.id)
    setActionError(null)
    try {
      await deleteRequest({ requestId: selected.id, appId: selected.appId })
      setHiddenRequestIds(prev => ({ ...prev, [selected.id]: true }))
      setSelectedId(null)
    } catch (error) {
      setActionError(error?.message || 'Support request could not be removed.')
    } finally {
      setBusyRequestId(null)
    }
  }

  const threadMessages = selected
    ? [...(selected.messages || []), ...(extraMessages[selected.id] || [])]
    : []

  if (requests.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border/40 bg-card/20 px-8 py-12 text-center">
        <p className="text-sm font-medium text-foreground">No support requests yet</p>
        <p className="mt-1 text-xs text-muted-foreground">
          When you send a request, the conversation will appear here.
        </p>
        {onNewSupport && (
          <button
            type="button"
            onClick={onNewSupport}
            className="mt-5 rounded-xl border border-border/40 px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            Send your first request
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex overflow-hidden rounded-2xl border border-border/30 bg-card/20" style={{ minHeight: 440 }}>

      {/* Left: app-grouped ticket list */}
      <div className="w-60 shrink-0 border-r border-border/20 flex flex-col bg-card/30">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border/15">
          <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Support</span>
          {onNewSupport && (
            <button
              type="button"
              onClick={onNewSupport}
              className="rounded-lg border border-border/40 px-2.5 py-1 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:border-border/70 transition-colors"
            >
              + New
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {groups.map(group => (
            <div key={group.appId}>
              <AppGroupHeader
                appLabel={group.appLabel}
                count={group.tickets.length}
                open={openGroups[group.appId] !== false}
                onToggle={() => toggleGroup(group.appId)}
              />
              {openGroups[group.appId] !== false && group.tickets.map(req => (
                <TicketRow
                  key={req.id}
                  req={req}
                  active={req.id === (selectedId || allTickets[0]?.id)}
                  onClick={() => setSelectedId(req.id)}
                />
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* Right: thread */}
      {selected && (
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-warning/20 bg-warning/[0.04]">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-warning/15 text-warning ring-1 ring-warning/20 text-xs font-bold">?</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap mb-0.5">
                <span className="text-[10px] font-mono text-warning/60">{selected.ticketId}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase ${
                  selected.status === 'resolved' ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning'
                }`}>{selected.status}</span>
                <span className="rounded-full border border-border/30 bg-muted/30 px-1.5 py-0.5 text-[9px] text-muted-foreground">
                  {selected.appLabel}
                </span>
              </div>
              <p className="text-sm font-semibold text-foreground truncate">{selected.subject}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => handleStatusChange(selected.status === 'resolved' ? 'open' : 'resolved')}
                disabled={busyRequestId === selected.id}
                className="rounded-lg border border-border/40 bg-card/70 px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:opacity-50"
              >
                {busyRequestId === selected.id ? 'Saving…' : selected.status === 'resolved' ? 'Reopen' : 'Close'}
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={busyRequestId === selected.id}
                className="rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-1 text-[11px] font-semibold text-destructive transition-colors hover:border-destructive/60 hover:bg-destructive/15 disabled:opacity-50"
              >
                Remove
              </button>
            </div>
          </div>

          <ChatThread
            messages={threadMessages}
            variant="support"
            emptyText="No messages yet."
            className="flex-1 min-h-0"
            inputPlaceholder="Reply to this ticket…"
            onSend={selected.status !== 'resolved' ? handleSend : undefined}
          />
          {actionError && (
            <div className="border-t border-destructive/20 px-4 py-2 text-xs text-destructive">
              {actionError}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
