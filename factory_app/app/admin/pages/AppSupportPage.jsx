import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  Panel,
  StatusPill,
  StudioErrorState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, { formatCompactNumber, formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

// ─── Demo sessions with placeholder transcripts ───────────────────────────────

const DEMO_SESSIONS = [
  {
    chat_id: 'demo-session-1',
    workflow_name: 'AppGenerator',
    user_id: 'sarah@example.com',
    agent_turns: 14,
    tool_calls: 52,
    errors: 0,
    started_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
    ended_at: null,
    status: 1,
    messages: [
      { role: 'user', content: 'Hi, I want to build an app that helps freelancers track their invoices and clients.' },
      { role: 'assistant', content: "That's a great idea! I'll help you scope this out. Let me start by asking a few questions about your core workflows. How do freelancers typically create invoices today — manually or with existing tools?" },
      { role: 'user', content: "Most of them do it manually with Word or Google Docs. It's a mess." },
      { role: 'assistant', content: "Understood — so the core pain point is friction around invoice creation and tracking payment status. I'll design the app around three core workflows: Invoice Creation, Client Management, and Payment Tracking. Let me generate a concept brief for you now." },
      { role: 'user', content: 'Yes exactly. Can you also add expense tracking?' },
      { role: 'assistant', content: "Absolutely — expense tracking maps naturally into the data model alongside invoices. I'll add an Expenses module with receipt capture and categorization. Generating your full app plan now…" },
    ],
  },
  {
    chat_id: 'demo-session-2',
    workflow_name: 'RevisionWorkflow',
    user_id: 'james@example.com',
    agent_turns: 8,
    tool_calls: 23,
    errors: 1,
    started_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    ended_at: new Date(Date.now() - 2.8 * 60 * 60 * 1000).toISOString(),
    status: 0,
    messages: [
      { role: 'user', content: 'I need to update the dashboard to show monthly revenue trends instead of daily.' },
      { role: 'assistant', content: "Got it. I'll update the dashboard page schema to group revenue by month and add a trend line chart primitive. This will also require a small change to the data aggregation query in the analytics module. Applying now…" },
      { role: 'user', content: 'And can you also add a filter for date range?' },
      { role: 'assistant', content: "Yes — I'll add a DateRangePicker control to the toolbar. One moment while I apply the revision…" },
      { role: 'system', content: 'Error: structured output validation failed — chart primitive config missing required field `y_axis_label`.' },
    ],
  },
  {
    chat_id: 'demo-session-3',
    workflow_name: 'AppGenerator',
    user_id: 'priya@example.com',
    agent_turns: 32,
    tool_calls: 118,
    errors: 0,
    started_at: new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
    ended_at: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
    status: 2,
    messages: [
      { role: 'user', content: 'I want to create a community platform for indie game developers to share projects and get feedback.' },
      { role: 'assistant', content: "Perfect — a community platform for indie devs is a great fit for Mozaiks. I'll structure this around Project Showcase, Feedback Threads, and Member Profiles as the core modules. Let me build your concept brief…" },
      { role: 'user', content: 'Can we also have a voting/rating system for projects?' },
      { role: 'assistant', content: "Yes — I'll add a Reactions module with upvotes, ratings, and a trending feed algorithm. This is built in. Generating your full app plan now…" },
      { role: 'assistant', content: "Your app is ready! I've generated all modules, pages, and workflows. You can review the build output in the Build History tab and deploy when ready." },
    ],
  },
  {
    chat_id: 'demo-session-4',
    workflow_name: 'DesignDocs',
    user_id: 'mike@example.com',
    agent_turns: 6,
    tool_calls: 18,
    errors: 0,
    started_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    ended_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000 + 8 * 60 * 1000).toISOString(),
    status: 2,
    messages: [
      { role: 'user', content: 'Can you review the data model for the Orders module and suggest improvements?' },
      { role: 'assistant', content: "Looking at the Orders schema now. I see a few things: the `order_items` array should be normalized into a separate collection for scale, and the `status` field would benefit from an explicit enum. Let me draft the updated design doc…" },
      { role: 'assistant', content: "Design documentation updated. The revised schema includes normalized order items, explicit status transitions, and indexed lookups on `user_id` and `created_at`. Ready for review." },
    ],
  },
]

const DEMO_OPERATORS = ['Unassigned', 'Sarah K.', 'James M.', 'Priya R.', 'Mike T.']

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sessionStatusTone(run) {
  if (Number(run.errors || 0) > 0 || run.status === 0) return 'destructive'
  if (!run.ended_at && Number(run.agent_turns || 0) > 30) return 'warning'
  if (run.status === 2) return 'success'
  if (run.status === 1) return 'primary'
  return 'default'
}

function sessionStatusLabel(run) {
  if (Number(run.errors || 0) > 0 || run.status === 0) return 'Error'
  if (!run.ended_at && Number(run.agent_turns || 0) > 30) return 'Stalled'
  if (run.status === 2) return 'Completed'
  if (run.status === 1) return 'Running'
  return 'Ended'
}

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

function stringHue(str) {
  let hash = 0
  for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash)
  return Math.abs(hash) % 360
}

function userInitials(userId) {
  if (!userId) return '?'
  const clean = userId.replace(/@.*$/, '').replace(/[._-]+/g, ' ').trim()
  const words = clean.split(/\s+/).filter(Boolean)
  if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase()
  return (words[0] || '?').slice(0, 2).toUpperCase()
}

function assignmentsKey(appId) {
  return `mozaiks_support_assignments_${appId}`
}

function loadAssignments(appId) {
  try { return JSON.parse(localStorage.getItem(assignmentsKey(appId)) || '{}') } catch { return {} }
}

function saveAssignments(appId, assignments) {
  try { localStorage.setItem(assignmentsKey(appId), JSON.stringify(assignments)) } catch (_) {}
}

// ─── Avatar ───────────────────────────────────────────────────────────────────

function UserAvatar({ userId, size = 'md' }) {
  const initials = userInitials(userId)
  const hue = stringHue(userId || '')
  const sz = size === 'sm' ? 'h-7 w-7 text-[10px]' : 'h-9 w-9 text-xs'
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full font-bold text-white shadow-sm ${sz}`}
      style={{ backgroundColor: `hsl(${hue} 55% 42%)` }}
      aria-hidden="true"
    >
      {initials}
    </span>
  )
}

// ─── Session list card ────────────────────────────────────────────────────────

function SessionListCard({ run, active, onClick }) {
  const tone = sessionStatusTone(run)
  const needsAttention = tone === 'destructive' || tone === 'warning'
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
            <span className="truncate text-xs font-semibold text-foreground/90">{run.workflow_name || 'Chat session'}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground/50">{relativeTime(run.started_at)}</span>
          </div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground/65">{run.user_id || 'Unknown user'}</div>
          <div className="mt-1.5">
            <StatusPill tone={tone}>{sessionStatusLabel(run)}</StatusPill>
          </div>
        </div>
      </div>
    </button>
  )
}

// ─── Message bubble ───────────────────────────────────────────────────────────

function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  const isOperator = message.role === 'operator'
  const isSystem = message.role === 'system'

  if (isSystem) {
    return (
      <div className="mx-auto max-w-sm rounded-xl border border-destructive/25 bg-destructive/8 px-3 py-2 text-center text-[11px] text-destructive/80">
        {message.content}
      </div>
    )
  }

  const alignRight = isUser || isOperator

  return (
    <div className={`flex gap-2.5 ${alignRight ? 'flex-row-reverse' : 'flex-row'}`}>
      {!alignRight && (
        <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/20 text-[10px] font-bold text-primary">
          AI
        </span>
      )}
      <div className="flex flex-col gap-0.5">
        {isOperator && (
          <span className="pr-1 text-right text-[10px] font-medium text-muted-foreground/50">Operator</span>
        )}
        <div
          className={[
            'max-w-[72%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed',
            isUser
              ? 'rounded-tr-sm bg-primary text-primary-foreground'
              : isOperator
                ? 'rounded-tr-sm bg-secondary/30 text-foreground border border-secondary/30'
                : 'rounded-tl-sm border border-border/30 bg-card/80 text-foreground',
          ].join(' ')}
        >
          {message.content}
        </div>
      </div>
    </div>
  )
}

// ─── Thread detail panel ──────────────────────────────────────────────────────

function ThreadPanel({ run, assignments, onAssign, operators }) {
  const [threadMessages, setThreadMessages] = useState(null)
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)

  // Reset thread messages when session changes
  useEffect(() => {
    setThreadMessages(null)
    setInput('')
  }, [run?.chat_id])

  if (!run) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/30 bg-card/40 px-6 py-12 text-center">
        <span className="text-3xl opacity-20">💬</span>
        <p className="text-sm text-muted-foreground/60">Select a session to review the conversation</p>
      </div>
    )
  }

  const baseMessages = run.messages || []
  const messages = threadMessages ?? baseMessages
  const assignedTo = assignments[run.chat_id] || 'Unassigned'
  const tone = sessionStatusTone(run)

  function handleSend() {
    const text = input.trim()
    if (!text) return
    const operatorMsg = { role: 'operator', content: text }
    setThreadMessages([...messages, operatorMsg])
    setInput('')
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
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
            <div className="text-[11px] text-muted-foreground/60">{run.workflow_name} · {relativeTime(run.started_at)}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone={tone}>{sessionStatusLabel(run)}</StatusPill>
          <select
            value={assignedTo}
            onChange={(e) => onAssign(run.chat_id, e.target.value)}
            className="rounded-lg border border-border/30 bg-background/60 px-2 py-1 text-[11px] text-foreground/65 transition-colors hover:border-border/60 focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            {operators.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length > 0 ? (
          <>
            {messages.map((msg, i) => <MessageBubble key={i} message={msg} />)}
            <div ref={bottomRef} />
          </>
        ) : (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <p className="text-sm text-muted-foreground/50">
              Transcript will appear here once the session completes.
            </p>
          </div>
        )}
      </div>

      {/* Stats bar */}
      <div className="flex items-center gap-3 border-t border-border/10 px-4 py-1.5 text-[11px] text-muted-foreground/45">
        <span>{formatCompactNumber(run.agent_turns, '0')} turns</span>
        <span className="text-border/30">·</span>
        <span>{formatCompactNumber(run.tool_calls, '0')} tools</span>
        {Number(run.errors || 0) > 0 ? (
          <>
            <span className="text-border/30">·</span>
            <span className="text-destructive/60">{run.errors} errors</span>
          </>
        ) : null}
      </div>

      {/* Chat input */}
      <div className="border-t border-border/20 px-3 pb-3 pt-2">
        <div className="flex items-end gap-2 rounded-xl border border-border/30 bg-background/60 px-3 py-2 focus-within:border-primary/40 focus-within:ring-1 focus-within:ring-primary/20 transition-all">
          <textarea
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Reply to this session…"
            className="flex-1 resize-none bg-transparent text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none"
            style={{ maxHeight: '96px', overflowY: 'auto' }}
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim()}
            className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-opacity disabled:opacity-30 hover:opacity-90"
            aria-label="Send"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
        <p className="mt-1 px-1 text-[10px] text-muted-foreground/35">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AppSupportPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])
  const [assignments, setAssignments] = useState(() => loadAssignments(appId))
  const [selectedId, setSelectedId] = useState(null)

  if (loading) return <StudioLoadingState label="Loading support inbox…" />
  if (error || !data?.summary) return <StudioErrorState title="Support Unavailable" message={error || 'No support data returned.'} />

  // Use DEMO_SESSIONS until a transcript API exists. Real runs have no messages
  // array and the right panel would always be empty. Swap this once
  // /api/admin/runs/:chatId/messages is wired up.
  const runs = DEMO_SESSIONS
  const selectedRun = runs.find((r) => r.chat_id === selectedId) || null

  const erroredCount = runs.filter((r) => Number(r.errors || 0) > 0 || r.status === 0).length
  const stalledCount = runs.filter((r) => !r.ended_at && Number(r.agent_turns || 0) > 30).length
  const feedbackItems = Array.isArray(snapshot.summary?.support?.feedback) ? snapshot.summary.support.feedback : []
  const poorFeedbackCount = feedbackItems.filter((f) => f.rating === 0).length
  const needsReview = erroredCount + stalledCount

  function handleAssign(chatId, operator) {
    const next = { ...assignments, [chatId]: operator }
    setAssignments(next)
    saveAssignments(appId, next)
  }

  const summaryItems = [
    { id: 'sessions', label: 'Total Sessions', value: formatCompactNumber(runs.length, '0'), detail: 'All chat sessions' },
    { id: 'errors', label: 'Sessions with Errors', value: formatCompactNumber(erroredCount, '0'), detail: 'Need triage' },
    { id: 'stalled', label: 'Stalled Sessions', value: formatCompactNumber(stalledCount, '0'), detail: 'No completion yet' },
    { id: 'feedback', label: 'Poor Ratings', value: formatCompactNumber(poorFeedbackCount, '0'), detail: 'From session feedback' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Support"
          subtitle="Review user sessions, assign operators, and follow up on issues."
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Inbox"
          title="Help desk"
          subtitle="User sessions appear here. Click a session to review the conversation and assign an operator."
          action={needsReview > 0
            ? <StatusPill tone="warning">{needsReview} need review</StatusPill>
            : <StatusPill tone="success">All clear</StatusPill>}
        >
          {/* Two-pane inbox */}
          <div className="flex gap-3" style={{ minHeight: '480px' }}>

            {/* Left: session list */}
            <div className="flex w-64 shrink-0 flex-col gap-1.5 overflow-y-auto xl:w-72">
              {runs.map((run) => (
                <SessionListCard
                  key={run.chat_id}
                  run={run}
                  active={run.chat_id === selectedId}
                  onClick={() => setSelectedId(run.chat_id === selectedId ? null : run.chat_id)}
                />
              ))}
            </div>

            {/* Right: thread detail */}
            <ThreadPanel
              run={selectedRun}
              assignments={assignments}
              onAssign={handleAssign}
              operators={DEMO_OPERATORS}
            />
          </div>
        </Panel>

        {feedbackItems.length > 0 && (
          <Panel
            eyebrow="Feedback"
            title="Session ratings"
            subtitle="Ratings submitted by users at the end of their chat sessions."
            action={poorFeedbackCount > 0
              ? <StatusPill tone="warning">{poorFeedbackCount} poor</StatusPill>
              : <StatusPill tone="success">All positive</StatusPill>}
          >
            <div className="space-y-2">
              {feedbackItems.slice(0, 10).map((item, i) => (
                <div key={item.session_id || i} className="flex items-center justify-between rounded-2xl border border-border/20 bg-card/65 px-4 py-3">
                  <div>
                    <div className="text-sm font-semibold text-foreground">{item.workflow_name || 'Session'}</div>
                    <div className="mt-1 text-xs text-muted-foreground/75">{formatDateTimeLabel(item.created_at)}</div>
                  </div>
                  <StatusPill tone={item.rating === 0 ? 'destructive' : 'success'}>
                    {item.rating === 0 ? 'Poor' : 'Good'}
                  </StatusPill>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>
    </WorkspaceLayout>
  )
}
