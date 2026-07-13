import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { ChatThread } from '@mozaiks/chat-ui/ui'
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
    chat_id: 'demo-session-0',
    workflow_name: 'AppGenerator',
    user_id: 'alex@example.com',
    agent_turns: 3,
    tool_calls: 4,
    errors: 0,
    started_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    ended_at: null,
    status: 1,
    awaiting_operator: true,
    messages: [
      { role: 'user', content: "I've been trying to get this working for an hour and I'm completely stuck. I need to talk to a real person." },
      { role: 'assistant', content: "I completely understand — that's frustrating and I'm sorry you've hit a wall. An operator will follow up with you shortly. In the meantime, is there anything specific I can note for them about what you were trying to do?" },
      { role: 'user', content: "Just tell them I need help with the invoicing module, it keeps erroring out on save." },
    ],
  },
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
  if (run.awaiting_operator) return 'warning'
  if (Number(run.errors || 0) > 0 || run.status === 0) return 'destructive'
  if (!run.ended_at && Number(run.agent_turns || 0) > 30) return 'warning'
  if (run.status === 2) return 'success'
  if (run.status === 1) return 'primary'
  return 'default'
}

function sessionStatusLabel(run) {
  if (run.awaiting_operator) return 'Awaiting operator'
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

// ─── Thread detail panel ──────────────────────────────────────────────────────

function ThreadPanel({ run, assignments, onAssign, operators }) {
  const [extraMessages, setExtraMessages] = useState([])

  useEffect(() => { setExtraMessages([]) }, [run?.chat_id])

  if (!run) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/30 bg-card/40 px-6 py-12 text-center">
        <span className="text-3xl opacity-20">💬</span>
        <p className="text-sm text-muted-foreground/60">Select a session to review the conversation</p>
      </div>
    )
  }

  const messages = [...(run.messages || []), ...extraMessages]
  const assignedTo = assignments[run.chat_id] || 'Unassigned'
  const tone = sessionStatusTone(run)

  function handleSend(text) {
    setExtraMessages((prev) => [...prev, { role: 'operator', content: text }])
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
        <div className="flex items-center gap-2 shrink-0">
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

      {/* ChatThread primitive handles messages + input */}
      <ChatThread
        messages={messages}
        onSend={handleSend}
        inputPlaceholder="Reply to this session…"
        emptyText="Transcript will appear here once the session completes."
        className="flex-1 min-h-0"
      />

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
