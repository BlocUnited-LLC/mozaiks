import { useMemo } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, { formatCompactNumber, formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot, sumBy } from './appStudioDataHelpers.js'
import { buildHealthState, validationLabel, validationTone } from './studioHealthModel.js'
import { useAppStudioData } from './useAppStudioData.js'


function RuntimeSignalsRow({ metrics }) {
  if (!metrics?.available) return null

  const turns = metrics.agent_turns || {}
  const llm = metrics.llm_calls || {}

  const errorTurns = Object.entries(turns.by_outcome || {})
    .filter(([k]) => k !== 'success')
    .reduce((sum, [, v]) => sum + v, 0)
  const hasErrors = errorTurns > 0
  const turnErrorRate = turns.total > 0 ? Math.round((errorTurns / turns.total) * 100) : 0

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-2xl border border-border/70 bg-card/60 px-5 py-3 text-sm">
      <span className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Live runtime</span>
      <span className="text-foreground">
        <span className="font-medium">{formatCompactNumber(turns.total, '0')}</span>
        <span className="ml-1 text-muted-foreground">agent turns</span>
      </span>
      {llm.avg_duration_seconds != null && (
        <span className="text-foreground">
          <span className="font-medium">{llm.avg_duration_seconds}s</span>
          <span className="ml-1 text-muted-foreground">avg latency</span>
        </span>
      )}
      <span className={hasErrors ? 'font-medium text-destructive' : 'text-muted-foreground'}>
        {hasErrors ? `${turnErrorRate}% error rate (${errorTurns} errors)` : 'No agent errors'}
      </span>
    </div>
  )
}

function Tile({ label, value, detail = null }) {
  return (
    <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-foreground">{value}</div>
      {detail ? <div className="mt-1 text-xs text-muted-foreground">{detail}</div> : null}
    </div>
  )
}

export default function AppHealthPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <StudioLoadingState label="Loading app health…" />
  if (error || !data?.summary) return <StudioErrorState title="Health Unavailable" message={error || 'No health summary returned.'} />

  // Every number on this page comes from an endpoint that returns it:
  // run counts from /api/admin/stats, recent runs from /api/admin/runs, the
  // build from /api/studio/build/history, connectors from /api/studio/integrations.
  const latestArtifact = snapshot.buildHistory[0] || null
  const latestValidationStatus = latestArtifact ? latestArtifact.validation_status || 'pending' : null
  const recentRuns = snapshot.runs
  const trackedRuns = Number(snapshot.stats.tracked_chats || 0)
  const failedRuns = Number(snapshot.stats.failed_chats || 0)
  const averageRunSeconds = recentRuns.length > 0
    ? Math.round(sumBy(recentRuns, (run) => run.runtime_sec || 0) / recentRuns.length)
    : null
  const integrationIssues = snapshot.appConnectors.filter((connector) => !connector.secret_available).length
  const health = buildHealthState({ latestValidationStatus, failedRuns, trackedRuns, integrationIssues })
  const averageRunLabel = averageRunSeconds != null ? `${averageRunSeconds}s` : 'No runs yet'
  const recentRunsDetail = recentRuns.length > 0 ? `Across the ${recentRuns.length} most recent` : null

  const summaryItems = [
    { id: 'status', label: 'Status', value: health.label, detail: health.issues.length > 0 ? `${health.issues.length} to look at` : 'Nothing to look at' },
    { id: 'build', label: 'Latest build', value: validationLabel(latestValidationStatus), detail: 'Validation result' },
    { id: 'failed', label: 'Failed runs', value: formatCompactNumber(failedRuns, '0'), detail: `Of ${formatCompactNumber(trackedRuns, '0')} recorded` },
    { id: 'runtime', label: 'Avg run time', value: averageRunLabel, detail: recentRunsDetail || 'Recent runs' },
    { id: 'integrations', label: 'Integration gaps', value: formatCompactNumber(integrationIssues, '0'), detail: 'Missing a credential' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Health"
          subtitle="What needs attention for this app: its latest build, runs that failed, and integrations missing a credential."
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.02fr)_minmax(0,0.98fr)]">
          <Panel eyebrow="Overall" title="Current app health" subtitle="Each item below is a specific thing to fix. An empty list means none of these checks found a problem.">
            <div className="rounded-[1.75rem] border border-border/70 bg-card/60 px-5 py-5">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Overall health</div>
                <StatusPill tone={health.tone}>{health.label}</StatusPill>
              </div>

              <div className="mt-4 space-y-3 text-sm text-muted-foreground">
                {health.issues.length > 0 ? health.issues.map((issue) => (
                  <div key={issue} className="rounded-2xl border border-border/70 bg-background/60 px-4 py-3">{issue}</div>
                )) : (
                  <div className="rounded-2xl border border-border/70 bg-background/60 px-4 py-3">
                    The latest build, recorded runs, and integration credentials show nothing to fix.
                  </div>
                )}
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Validation" title="Release confidence" subtitle="The newest build and whether it passed validation.">
            <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Latest build artifact</div>
                  <div className="mt-2 font-semibold text-foreground">{latestArtifact ? `Build version ${latestArtifact.version_number}` : 'No build version yet'}</div>
                </div>
                {latestArtifact ? <StatusPill tone={validationTone(latestValidationStatus)}>{validationLabel(latestValidationStatus)}</StatusPill> : null}
              </div>
              <div className="mt-2 text-sm text-muted-foreground">{latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'Validation will appear here once a build version exists.'}</div>
            </div>
          </Panel>
        </div>

        <RuntimeSignalsRow metrics={data?.runtimeMetrics} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.96fr)_minmax(0,1.04fr)]">
          <Panel eyebrow="Workflows" title="Workflow reliability" subtitle="How many of this app's runs were recorded, how many failed, and how long recent ones took.">
            <div className="grid gap-3 sm:grid-cols-3">
              <Tile label="Recorded runs" value={formatCompactNumber(trackedRuns, '0')} />
              <Tile label="Failed runs" value={formatCompactNumber(failedRuns, '0')} />
              <Tile label="Average run time" value={averageRunLabel} detail={recentRunsDetail} />
            </div>
          </Panel>

          <Panel eyebrow="Integrations" title="Integration posture" subtitle="Missing credentials or incomplete connectors should surface here as health blockers.">
            {snapshot.appConnectors.length > 0 ? (
              <div className="space-y-3">
                {snapshot.appConnectors.map((connector) => (
                  <div key={connector.service} className="flex items-center justify-between rounded-2xl border border-border bg-card/70 px-4 py-3">
                    <div>
                      <div className="font-semibold text-foreground">{connector.display_name || connector.service}</div>
                      <div className="mt-1 text-sm text-muted-foreground">{connector.secret_available ? 'Credential is configured.' : 'Credential still needs to be saved.'}</div>
                    </div>
                    <StatusPill tone={connector.secret_available ? 'success' : 'warning'}>{connector.secret_available ? 'Ready' : 'Needs secret'}</StatusPill>
                  </div>
                ))}
              </div>
            ) : (
              <StudioInlineEmptyState
                title="No integrations connected yet"
                description="Integration health will appear here once this app starts depending on external services."
              />
            )}
          </Panel>
        </div>

      </div>
    </WorkspaceLayout>
  )
}
