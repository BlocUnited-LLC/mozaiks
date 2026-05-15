import { useMemo } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ConsoleErrorState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/ConsoleShared.jsx'
import AppConsoleHero, {
  formatCompactNumber,
  formatCurrencyValue,
  formatDateTimeLabel,
} from './AppConsoleChrome.jsx'
import { getAppConsoleSnapshot } from './appConsoleDataHelpers.js'
import {
  getAppJourneyLabel,
  getApprovalStateLabel,
  getPlanStateLabel,
  getRuntimeHealthLabel,
  getRuntimeReadinessLabel,
} from './appConsoleModel.js'
import { useAppConsoleData } from './useAppConsoleData.js'


function getWorkflowNames(snapshot) {
  return Array.from(new Set([
    ...snapshot.workflowNames,
    ...snapshot.runs.map((run) => run.workflow_name).filter(Boolean),
  ]))
}

function validationTone(value) {
  if (value === 'passed') return 'success'
  if (value === 'failed') return 'destructive'
  return 'warning'
}

export default function AppOverviewPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const snapshot = useMemo(() => getAppConsoleSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <ConsoleLoadingState label="Loading App Overview…" />
  if (error || !data?.summary) return <ConsoleErrorState title="App Overview Unavailable" message={error || 'No summary returned.'} />

  const build = data.buildState?.build || {}
  const latestArtifact = snapshot.buildHistory[0] || null
  const latestRun = snapshot.runs[0] || null
  const workflowNames = getWorkflowNames(snapshot)
  const nextStep = snapshot.summary?.next_step || 'Continue from the highest-priority build or runtime signal.'
  const summaryItems = [
    {
      id: 'journey',
      label: 'Journey',
      value: getAppJourneyLabel(snapshot.app.journey),
      detail: getRuntimeReadinessLabel(snapshot.summary.workspace?.runtime_readiness),
    },
    {
      id: 'workflows',
      label: 'Workflow Count',
      value: formatCompactNumber(workflowNames.length, '0'),
      detail: workflowNames[0] || 'No workflows connected yet',
    },
    {
      id: 'latest',
      label: 'Latest Build',
      value: latestArtifact ? `v${latestArtifact.version_number}` : 'Pending',
      detail: latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'No saved build versions',
    },
    {
      id: 'runtime',
      label: 'Runtime Health',
      value: getRuntimeHealthLabel(snapshot.lifecycleState, snapshot.stats.total_errors),
      detail: formatCurrencyValue(snapshot.stats.total_cost, 'Pending'),
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title="Overview"
          subtitle="Review build state, workflow coverage, approvals, and runtime signals."
          currentSection="overview"
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.08fr)_minmax(0,0.92fr)]">
          <Panel title="Latest app movement" subtitle="Most recent build and runtime activity.">
            <div className="space-y-3">
              <div className="rounded-2xl border border-primary/24 bg-primary/8 px-4 py-4">
                <div className="text-[12px] font-medium text-muted-foreground/82">Next step</div>
                <div className="mt-2 text-sm leading-6 text-foreground">{nextStep}</div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-[12px] font-medium text-muted-foreground/82">Latest build artifact</div>
                    <div className="mt-1 text-base font-semibold text-foreground">
                      {latestArtifact ? `Build version ${latestArtifact.version_number}` : 'No build version yet'}
                    </div>
                  </div>
                  {latestArtifact ? (
                    <StatusPill tone={validationTone(latestArtifact.validation_status)}>
                      {latestArtifact.validation_status || 'pending'}
                    </StatusPill>
                  ) : null}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'Build history will appear once a version is generated.'}
                </div>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                <div className="text-[12px] font-medium text-muted-foreground/82">Latest runtime activity</div>
                <div className="mt-1 text-base font-semibold text-foreground">
                  {latestRun?.workflow_name || 'No runtime activity yet'}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {latestRun ? `${latestRun.user_id || 'Operator'} · ${formatDateTimeLabel(latestRun.started_at)}` : 'Runs will appear once Build or Operations records activity.'}
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="Workflow coverage" subtitle="Connected workflows for this app.">
            <div className="space-y-3">
              {workflowNames.length > 0 ? workflowNames.map((workflowName) => (
                <div key={workflowName} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4">
                  <div className="font-semibold text-foreground">{workflowName}</div>
                  <div className="mt-1 text-sm text-muted-foreground">Available to the current app console workflow set.</div>
                </div>
              )) : (
                <div className="rounded-2xl border border-dashed border-border/42 bg-background/24 px-4 py-6 text-sm text-muted-foreground">
                  No workflow coverage has been recorded yet.
                </div>
              )}
            </div>
          </Panel>
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.96fr)_minmax(0,1.04fr)]">
          <Panel title="Pending Approvals" subtitle="Current plan and approval state.">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone={build.approval_state === 'approved' ? 'success' : build.approval_state === 'rejected' ? 'warning' : 'primary'}>
                  {getApprovalStateLabel(build.approval_state)}
                </StatusPill>
                <StatusPill tone="default">{getPlanStateLabel(build.plan_state)}</StatusPill>
              </div>

              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Current request</div>
                <div className="mt-2 text-foreground">
                  {build.current_request?.text || 'The current request brief will appear here after Build saves the draft.'}
                </div>
                <div className="mt-3 text-muted-foreground">
                  Request kind: {build.current_request?.request_kind || 'Not captured yet'}
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="Operating summary" subtitle="Cost, errors, and tool activity.">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Tool Calls</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.stats.total_tool_calls, '0')}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Errors</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.stats.total_errors, '0')}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Total Cost</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCurrencyValue(snapshot.stats.total_cost, 'Pending')}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Latest Activity</div>
                <div className="mt-2 text-sm font-semibold text-foreground">{latestRun?.workflow_name || 'No runs yet'}</div>
                <div className="mt-1 text-muted-foreground">{latestRun ? formatDateTimeLabel(latestRun.started_at) : 'Waiting for the first saved run.'}</div>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
