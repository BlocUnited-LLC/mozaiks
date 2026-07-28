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
import { buildHealthState, formatPercentValue } from './studioHealthModel.js'
import { useAppStudioData } from './useAppStudioData.js'


function RuntimeSignalsPanel({ metrics }) {
  if (!metrics?.available) return null

  const turns = metrics.agent_turns || {}
  const llm = metrics.llm_calls || {}
  const tokens = metrics.llm_tokens || {}
  const tools = metrics.tool_calls || {}

  const errorTurns = Object.entries(turns.by_outcome || {})
    .filter(([k]) => k !== 'success')
    .reduce((sum, [, v]) => sum + v, 0)
  const turnErrorRate = turns.total > 0 ? Math.round((errorTurns / turns.total) * 100) : 0
  const toolErrors = Object.entries(tools.by_outcome || {})
    .filter(([k]) => k !== 'success')
    .reduce((sum, [, v]) => sum + v, 0)

  const topModels = Object.entries(llm.by_model || {})
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)

  return (
    <Panel eyebrow="Live" title="Runtime signals" subtitle="Real-time agent and LLM call metrics since last server start. Enable AG2_METRICS_ENABLED to activate.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
          <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Agent turns</div>
          <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(turns.total, '0')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{turnErrorRate}% error rate</div>
        </div>
        <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
          <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">LLM calls</div>
          <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(llm.total, '0')}</div>
          <div className="mt-1 text-sm text-muted-foreground">
            {llm.avg_duration_seconds != null ? `${llm.avg_duration_seconds}s avg` : 'No latency yet'}
          </div>
        </div>
        <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
          <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Tokens used</div>
          <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(tokens.total, '0')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{formatCompactNumber(tokens.input, '0')} in · {formatCompactNumber(tokens.output, '0')} out</div>
        </div>
        <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
          <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Tool calls</div>
          <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(tools.total, '0')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{toolErrors > 0 ? `${toolErrors} errors` : 'No errors'}</div>
        </div>
      </div>
      {topModels.length > 0 && (
        <div className="mt-3 space-y-2">
          {topModels.map(([model, count]) => (
            <div key={model} className="flex items-center justify-between rounded-2xl border border-border/70 bg-background/60 px-4 py-2 text-sm">
              <span className="font-medium text-foreground">{model}</span>
              <span className="text-muted-foreground">{formatCompactNumber(count, '0')} calls</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}

export default function AppHealthPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <StudioLoadingState label="Loading app health…" />
  if (error || !data?.summary) return <StudioErrorState title="Health Unavailable" message={error || 'No health summary returned.'} />

  const latestArtifact = snapshot.buildHistory[0] || null
  const averageLatency = snapshot.runs.length > 0
    ? Math.round(sumBy(snapshot.runs, (run) => run.runtime_sec || 0) / snapshot.runs.length)
    : null
  const integrationIssues = snapshot.appConnectors.filter((connector) => !connector.secret_available).length
  const health = buildHealthState({
    status: snapshot.lifecycleState,
    totalErrors: Number(snapshot.stats.total_errors || 0),
    runtimeReadiness: snapshot.summary.workspace?.runtime_readiness,
    latestValidationStatus: latestArtifact?.validation_status,
    uptimePercent: snapshot.deploymentRecord?.uptime_percent ?? null,
    hasDeploymentFailure: Boolean(snapshot.deploymentRecord?.failed),
    integrationIssues,
  })
  const errorRate = snapshot.runs.length > 0
    ? Math.round((Number(snapshot.stats.total_errors || 0) / snapshot.runs.length) * 100)
    : 0
  const summaryItems = [
    { id: 'score', label: 'Health Score', value: `${health.score}/100`, detail: health.label },
    { id: 'uptime', label: 'Uptime', value: health.uptimeLabel, detail: 'Observed hosting uptime' },
    { id: 'errors', label: 'Error Rate', value: snapshot.runs.length > 0 ? `${errorRate}%` : '0%', detail: 'Runs with recent friction' },
    { id: 'latency', label: 'Avg Latency', value: averageLatency != null ? `${averageLatency}s` : 'Pending', detail: 'Observed across recent runs' },
    { id: 'integrations', label: 'Integration Gaps', value: formatCompactNumber(integrationIssues, '0'), detail: 'Setup items still incomplete' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Health"
          currentSection="Health"
          subtitle="See the overall health of the app itself across runtime posture, workflow reliability, hosting, and integrations."
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.02fr)_minmax(0,0.98fr)]">
          <Panel eyebrow="Overall" title="Current app health" subtitle="Keep the overall health signal explicit before jumping into deeper app management pages.">
            <div className="rounded-[1.75rem] border border-border/70 bg-card/60 px-5 py-5">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Overall health</div>
                  <div className="mt-2 text-4xl font-semibold text-foreground">{health.score}/100</div>
                </div>
                <StatusPill tone={health.tone}>{health.label}</StatusPill>
              </div>

              <div className="mt-4 space-y-3 text-sm text-muted-foreground">
                {health.issues.length > 0 ? health.issues.map((issue) => (
                  <div key={issue} className="rounded-2xl border border-border/70 bg-background/60 px-4 py-3">{issue}</div>
                )) : (
                  <div className="rounded-2xl border border-border/70 bg-background/60 px-4 py-3">No major health risks are currently highlighted for this app.</div>
                )}
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Validation" title="Release confidence" subtitle="Tie health back to the latest artifact, runtime readiness, and deployment posture.">
            <div className="space-y-3">
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Latest build artifact</div>
                    <div className="mt-2 font-semibold text-foreground">{latestArtifact ? `Build version ${latestArtifact.version_number}` : 'No build version yet'}</div>
                  </div>
                  {latestArtifact ? <StatusPill tone={latestArtifact.validation_status === 'passed' ? 'success' : latestArtifact.validation_status === 'failed' ? 'destructive' : 'warning'}>{latestArtifact.validation_status || 'pending'}</StatusPill> : null}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">{latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'Validation will appear here once a build version exists.'}</div>
              </div>
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Runtime readiness</div>
                <div className="mt-2 font-semibold text-foreground">{health.runtimeLabel}</div>
              </div>
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Hosting uptime</div>
                <div className="mt-2 font-semibold text-foreground">{formatPercentValue(snapshot.deploymentRecord?.uptime_percent)}</div>
              </div>
            </div>
          </Panel>
        </div>

        <RuntimeSignalsPanel metrics={data?.runtimeMetrics} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.96fr)_minmax(0,1.04fr)]">
          <Panel eyebrow="Workflows" title="Workflow reliability" subtitle="Use the workflow panel to spot run volume, latency, and error concentration without opening raw traces.">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Workflow runs</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.runs.length, '0')}</div>
              </div>
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Runtime errors</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.stats.total_errors, '0')}</div>
              </div>
              <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Average latency</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{averageLatency != null ? `${averageLatency}s` : 'Pending'}</div>
              </div>
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
