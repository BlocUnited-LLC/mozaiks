import { useMemo } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton,
  Metric,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, { formatCompactNumber, formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

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

export default function AppSupportPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <StudioLoadingState label="Loading support inbox…" />
  if (error || !data?.summary) return <StudioErrorState title="Support Unavailable" message={error || 'No support data returned.'} />

  const runs = snapshot.runs
  const erroredCount = runs.filter((r) => Number(r.errors || 0) > 0 || r.status === 0).length
  const stalledCount = runs.filter((r) => !r.ended_at && Number(r.agent_turns || 0) > 30).length
  const reviewRuns = runs.filter((r) => Number(r.errors || 0) > 0 || r.status === 0 || (!r.ended_at && Number(r.agent_turns || 0) > 30))
  const feedbackItems = Array.isArray(snapshot.summary?.support?.feedback) ? snapshot.summary.support.feedback : []
  const poorFeedbackCount = feedbackItems.filter((f) => f.rating === 0).length

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
          subtitle="Chat sessions from users. Open a session to review the conversation or follow up with the user."
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Inbox"
          title="Help desk"
          subtitle="All sessions from users. Sessions with errors or poor ratings need operator attention."
          action={erroredCount + stalledCount > 0
            ? <StatusPill tone="warning">{erroredCount + stalledCount} need review</StatusPill>
            : <StatusPill tone="success">All clear</StatusPill>}
        >
          {runs.length === 0 ? (
            <StudioInlineEmptyState
              title="No chat sessions yet"
              description="User chat sessions will appear here once people start workflows or ask sessions on this app."
            />
          ) : (
            <div className="space-y-2">
              {runs.map((run) => {
                const runKey = run.chat_id || `${run.workflow_name || 'run'}-${run.started_at}`
                const openUrl = run.chat_id
                  ? `/chat?mode=workflow&chat_id=${encodeURIComponent(run.chat_id)}`
                  : null
                return (
                  <div key={runKey} className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-semibold text-foreground">
                          {run.workflow_name || 'Chat session'}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground/75">
                          {run.user_id || 'Unknown user'} · {formatDateTimeLabel(run.started_at)}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <StatusPill tone={sessionStatusTone(run)}>{sessionStatusLabel(run)}</StatusPill>
                        {openUrl ? (
                          <LinkButton to={openUrl} variant="outline" size="sm">Open</LinkButton>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                      <span>{formatCompactNumber(run.agent_turns, '0')} turns</span>
                      <span>{formatCompactNumber(run.tool_calls, '0')} tools</span>
                      <span>{formatCompactNumber(run.errors, '0')} errors</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Panel>

        <Panel
          eyebrow="Diagnostics"
          title="Run review"
          subtitle="Sessions that stalled, errored, or need operator follow-up."
          action={reviewRuns.length > 0
            ? <StatusPill tone="warning">{reviewRuns.length} open</StatusPill>
            : <StatusPill tone="success">All clear</StatusPill>}
        >
          {reviewRuns.length === 0 ? (
            <StudioInlineEmptyState
              title="No sessions need review"
              description="Stalled and errored sessions will appear here when the app needs operator attention."
            />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {reviewRuns.slice(0, 6).map((run) => (
                <div key={run.chat_id || `${run.workflow_name || 'run'}-${run.started_at}`} className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-foreground">
                        {run.workflow_name || 'Chat session'}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground/75">
                        {run.user_id || 'Unknown user'} · {formatDateTimeLabel(run.started_at)}
                      </div>
                    </div>
                    <StatusPill tone={sessionStatusTone(run)}>{sessionStatusLabel(run)}</StatusPill>
                  </div>
                </div>
              ))}
            </div>
          )}
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
                <div key={item.session_id || i} className="flex items-center justify-between rounded-2xl border border-border bg-card/70 px-4 py-3">
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
