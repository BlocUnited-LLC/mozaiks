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
 * Falls back to demo data when no real API data is present (OSS/local dev).
 */

import { useState } from 'react'
import { ChatThread } from '@mozaiks/chat-ui/ui'

// ─── Demo fallback ────────────────────────────────────────────────────────────

const DEMO_REQUESTS = [
  {
    request_id: 'sup-8821',
    ticket_id: 'SUP-8821',
    app_id: 'my-saas-app',
    app_label: 'My SaaS App',
    subject: 'App not loading after update',
    status: 'open',
    updated_at: new Date(Date.now() - 2 * 3600000).toISOString(),
    message: "I can see the issue on our end — pushing a fix now",
    messages: [
      { role: 'user',     content: 'My app stopped loading after the latest update. I just see a white screen.' },
      { role: 'operator', content: "Thanks for reaching out! I can see the issue — config sync missed your workspace. Pushing a fix now.", senderLabel: 'Jordan · Support' },
      { role: 'user',     content: 'How long will it take?' },
      { role: 'operator', content: "Under 5 minutes. You'll get an email when done. Let me know if anything else is blocked.", senderLabel: 'Jordan · Support' },
    ],
  },
  {
    request_id: 'sup-8790',
    ticket_id: 'SUP-8790',
    app_id: 'my-saas-app',
    app_label: 'My SaaS App',
    subject: 'Usage breakdown for last month',
    status: 'resolved',
    updated_at: new Date(Date.now() - 86400000).toISOString(),
    message: "Report delivered — marking resolved",
    messages: [
      { role: 'user',     content: 'Can I get a usage breakdown for last month? Workflow runs and token consumption by workflow.' },
      { role: 'operator', content: "Absolutely. CSV in 24 hours — anything else to include?", senderLabel: 'Taylor · Support' },
      { role: 'user',     content: 'That covers it, thanks.' },
      { role: 'operator', content: "Report sent. Marking resolved — feel free to reopen.", senderLabel: 'Taylor · Support' },
      { role: 'system',   content: 'Resolved · SUP-8790' },
    ],
  },
  {
    request_id: 'sup-8750',
    ticket_id: 'SUP-8750',
    app_id: 'community-hub',
    app_label: 'Community Hub',
    subject: 'Custom domain not propagating',
    status: 'open',
    updated_at: new Date(Date.now() - 3 * 86400000).toISOString(),
    message: "We can see the DNS record — propagation usually takes up to 48h",
    messages: [
      { role: 'user',     content: 'Added the CNAME but the custom domain still shows an error after 24h.' },
      { role: 'operator', content: "We can see the DNS record — propagation can take up to 48h. If still failing after that, let us know.", senderLabel: 'Sam · Support' },
    ],
  },
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

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
  return {
    id:        r.request_id || r.id,
    ticketId:  r.ticket_id || r.ticketId || r.request_id || r.id,
    appId:     r.app_id || 'platform',
    appLabel:  r.app_label || r.app_name || r.app_id || 'Platform',
    subject:   r.subject || r.page_title || r.message?.slice(0, 60) || 'Support request',
    status:    r.status || 'open',
    updatedAt: r.updated_at || r.updatedAt || r.created_at,
    messages:  r.messages || [{ role: 'user', content: r.message || '' }],
  }
}

async function postMessage(requestId, message) {
  try {
    await fetch('/api/modules/workspace_support/add_support_message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId, message, sender_role: 'user' }),
    })
  } catch (_) {}
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
  const rawRequests = data?.requests?.length > 0 ? data.requests : DEMO_REQUESTS
  const requests = rawRequests.map(normaliseRequest)

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
  const [selectedId, setSelectedId] = useState(firstTicketId)
  const [openGroups, setOpenGroups] = useState(() => Object.fromEntries(groups.map(g => [g.appId, true])))
  const [extraMessages, setExtraMessages] = useState({})

  const allTickets = groups.flatMap(g => g.tickets)
  const selected = allTickets.find(r => r.id === selectedId) || allTickets[0] || null

  function toggleGroup(appId) {
    setOpenGroups(prev => ({ ...prev, [appId]: !prev[appId] }))
  }

  function handleSend(text) {
    if (!selected) return
    setExtraMessages(prev => ({
      ...prev,
      [selected.id]: [...(prev[selected.id] || []), { role: 'user', content: text }],
    }))
    postMessage(selected.id, text)
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
          </div>

          <ChatThread
            messages={threadMessages}
            variant="support"
            emptyText="No messages yet."
            className="flex-1 min-h-0"
            inputPlaceholder="Reply to this ticket…"
            onSend={selected.status !== 'resolved' ? handleSend : undefined}
          />
        </div>
      )}
    </div>
  )
}
