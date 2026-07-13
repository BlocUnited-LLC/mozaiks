import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton,
  Metric,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, { formatCompactNumber, formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

// ─── Demo fallback sessions ───────────────────────────────────────────────────

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

function feedbackTone(rating) {
  if (rating === 0) return 'destructive'
  if (rating === 1) return 'success'
  return 'default'
}

function feedbackLabel(rating) {
  if (rating === 0) return 'Poor'
  if (rating === 1) return 'Good'
  return 'Rated'
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

// Deterministic hue from a string — used for user avatar color
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
  try {
    return JSON.parse(localStorage.getItem(assignmentsKey(appId)) || '{}')
  } catch {
    return {}
  }
}

function saveAssignments(appId, assignments) {
  try {
    localStorage.setItem(assignmentsKey(appId), JSON.stringify(assignments))
  } catch (_) {}
}

// ─── User avatar ──────────────────────────────────────────────────────────────

function UserAvatar({ userId }) {
  const initials = userInitials(userId)
  const hue = stringHue(userId || '')
  return (
    <span
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white shadow-sm"
      style={{ backgroundColor: `hsl(${hue} 55% 42%)` }}
      aria-hidden="true"
    >
      {initials}
    </span>
  )
}

// ─── Session card (inbox style) ───────────────────────────────────────────────

function SessionCard({ run, appId, assignments, onAssign, operators, active = false }) {
  const tone = sessionStatusTone(run)
  const label = sessionStatusLabel(run)
  const openUrl = run.chat_id
    ? `/chat?mode=workflow&chat_id=${encodeURIComponent(run.chat_id)}`
    : null
  const timeLabel = relativeTime(run.started_at)
  const assignedTo = assignments[run.chat_id] || 'Unassigned'
  const needsAttention = tone === 'destructive' || tone === 'warning'

  return (
    <div
      className={[
        'group relative overflow-hidden rounded-2xl border px-4 py-4 transition-all duration-200',
        active
          ? 'border-primary/70 bg-primary/15 shadow-[0_10px_35px_rgba(6,182,212,0.15)]'
          : needsAttention
            ? 'border-destructive/30 bg-card/70 hover:border-destructive/50 hover:bg-card/85'
            : 'border-border/20 bg-card/65 hover:border-border/40 hover:bg-card/80',
      ].join(' ')}
    >
      {/* Top row: avatar + title + time + status */}
      <div className="flex items-start gap-3">
        <UserAvatar userId={run.user_id} />

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {run.workflow_name || 'Chat session'}
              </div>
              <div className="mt-0.5 truncate text-xs text-muted-foreground/70">
                {run.user_id || 'Unknown user'}
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1.5">
              <span className="text-[10px] text-muted-foreground/50">{timeLabel}</span>
              <StatusPill tone={tone}>{label}</StatusPill>
            </div>
          </div>

          {/* Stats row */}
          <div className="mt-2.5 flex flex-wrap items-center gap-3 text-xs text-muted-foreground/60">
            <span>{formatCompactNumber(run.agent_turns, '0')} turns</span>
            <span className="text-border/50">·</span>
            <span>{formatCompactNumber(run.tool_calls, '0')} tools</span>
            {Number(run.errors || 0) > 0 ? (
              <>
                <span className="text-border/50">·</span>
                <span className="font-medium text-destructive/80">{run.errors} {run.errors === 1 ? 'error' : 'errors'}</span>
              </>
            ) : null}
          </div>

          {/* Footer: assign + open */}
          <div className="mt-3 flex items-center justify-between gap-3">
            <select
              value={assignedTo}
              onChange={(e) => onAssign(run.chat_id, e.target.value)}
              onClick={(e) => e.stopPropagation()}
              className="rounded-lg border border-border/30 bg-background/60 px-2 py-1 text-xs text-foreground/70 transition-colors hover:border-border/60 focus:outline-none focus:ring-1 focus:ring-primary/40"
            >
              {operators.map((op) => (
                <option key={op} value={op}>{op}</option>
              ))}
            </select>
            {openUrl ? (
              <LinkButton to={openUrl} variant="outline" size="sm" className="shrink-0">
                Open chat
              </LinkButton>
            ) : null}
          </div>
        </div>
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

  if (loading) return <StudioLoadingState label="Loading support inbox…" />
  if (error || !data?.summary) return <StudioErrorState title="Support Unavailable" message={error || 'No support data returned.'} />

  const liveRuns = snapshot.runs
  const runs = liveRuns.length > 0 ? liveRuns : DEMO_SESSIONS
  const isDemo = liveRuns.length === 0

  const erroredCount = runs.filter((r) => Number(r.errors || 0) > 0 || r.status === 0).length
  const stalledCount = runs.filter((r) => !r.ended_at && Number(r.agent_turns || 0) > 30).length
  const feedbackItems = Array.isArray(snapshot.summary?.support?.feedback) ? snapshot.summary.support.feedback : []
  const poorFeedbackCount = feedbackItems.filter((f) => f.rating === 0).length

  const operators = DEMO_OPERATORS

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

  const needsReview = erroredCount + stalledCount

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Support"
          subtitle="Chat sessions from users. Open a session to review the conversation or follow up."
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Inbox"
          title="Help desk"
          subtitle={isDemo
            ? 'Demo sessions shown — real sessions will appear here once users start workflows on this app.'
            : 'All sessions from users. Sessions with errors or poor ratings need operator attention.'}
          action={needsReview > 0
            ? <StatusPill tone="warning">{needsReview} need review</StatusPill>
            : <StatusPill tone="success">All clear</StatusPill>}
        >
          <div className="space-y-2">
            {runs.map((run) => {
              const runKey = run.chat_id || `${run.workflow_name || 'run'}-${run.started_at}`
              return (
                <SessionCard
                  key={runKey}
                  run={run}
                  appId={appId}
                  assignments={assignments}
                  onAssign={handleAssign}
                  operators={operators}
                />
              )
            })}
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
            <div className="mb-4 grid grid-cols-3 gap-3">
              <Metric label="Total ratings" value={formatCompactNumber(feedbackItems.length, '0')} />
              <Metric label="Poor" value={formatCompactNumber(poorFeedbackCount, '0')} detail="👎" />
              <Metric label="Good" value={formatCompactNumber(feedbackItems.length - poorFeedbackCount, '0')} detail="👍" />
            </div>
            <div className="space-y-2">
              {feedbackItems.slice(0, 10).map((item, i) => (
                <div key={item.session_id || i} className="flex items-center justify-between rounded-2xl border border-border/20 bg-card/65 px-4 py-3">
                  <div>
                    <div className="text-sm font-semibold text-foreground">{item.workflow_name || 'Session'}</div>
                    <div className="mt-1 text-xs text-muted-foreground/75">{formatDateTimeLabel(item.created_at)}</div>
                  </div>
                  <StatusPill tone={feedbackTone(item.rating)}>{feedbackLabel(item.rating)}</StatusPill>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>
    </WorkspaceLayout>
  )
}
