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
import CarryForwardReportSummary from './CarryForwardReportSummary.jsx'
import AppStudioHero, { formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'


function validationTone(status) {
  if (status === 'passed') return 'success'
  if (status === 'failed') return 'destructive'
  return 'warning'
}

function lifecycleTone(status) {
  if (status === 'current') return 'success'
  if (status === 'draft') return 'default'
  if (status === 'stale' || status === 'superseded') return 'warning'
  return 'default'
}

export default function AppBuildHistoryPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <StudioLoadingState label="Loading build history…" />
  if (error || !data?.summary) return <StudioErrorState title="Build History Unavailable" message={error || 'No summary returned.'} />

  const buildHistory = snapshot.buildHistory || []
  const latestArtifact = buildHistory[0] || null
  const summaryItems = [
    {
      id: 'versions',
      label: 'Build Versions',
      value: String(buildHistory.length || 0),
      detail: latestArtifact ? `Latest: v${latestArtifact.version_number}` : 'No versions yet',
    },
    {
      id: 'latest',
      label: 'Latest Build',
      value: latestArtifact ? `v${latestArtifact.version_number}` : 'Pending',
      detail: latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'No saved build versions',
    },
    {
      id: 'validation',
      label: 'Latest Validation',
      value: latestArtifact?.validation_status || 'Pending',
      detail: latestArtifact?.lifecycle_status || 'No status',
    },
    {
      id: 'carry_forward',
      label: 'Carry-forward Reports',
      value: String(buildHistory.filter((v) => v?.commit_metadata?.metadata?.carry_forward_report).length),
      detail: 'Builds with preservation audit',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title="Build History"
          subtitle="Artifact versions produced by build and refinement workflows, with carry-forward preservation audit."
          currentSection="activity"
          summaryItems={summaryItems}
        />

        <Panel
          title="Artifact versions"
          subtitle="Each entry is a saved build artifact. Expand carry-forward to see module preservation decisions."
        >
          {buildHistory.length === 0 ? (
            <StudioInlineEmptyState
              title="No build versions yet"
              description="Build history will appear here after the first successful AppGenerator run."
            />
          ) : (
            <div className="space-y-3">
              {buildHistory.map((artifact) => {
                if (!artifact) return null
                const cfReport = artifact?.commit_metadata?.metadata?.carry_forward_report || null
                return (
                  <div
                    key={artifact.id || artifact.version_number}
                    className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-[12px] font-medium text-muted-foreground/82">
                          Build artifact
                        </div>
                        <div className="mt-1 text-base font-semibold text-foreground">
                          v{artifact.version_number}
                        </div>
                        <div className="mt-0.5 text-sm text-muted-foreground">
                          {formatDateTimeLabel(artifact.created_at)}
                        </div>
                        {artifact.commit_metadata?.message && (
                          <div className="mt-1 text-[12px] text-muted-foreground/70">
                            {artifact.commit_metadata.message}
                          </div>
                        )}
                        {artifact.commit_metadata?.source_workflow && (
                          <div className="mt-0.5 text-[11px] text-muted-foreground/56">
                            {artifact.commit_metadata.source_workflow}
                          </div>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusPill tone={validationTone(artifact.validation_status)}>
                          {artifact.validation_status || 'pending'}
                        </StatusPill>
                        <StatusPill tone={lifecycleTone(artifact.lifecycle_status)}>
                          {artifact.lifecycle_status || 'draft'}
                        </StatusPill>
                      </div>
                    </div>

                    {cfReport && (
                      <CarryForwardReportSummary report={cfReport} />
                    )}
                    {!cfReport && (
                      <div className="mt-2 text-[11px] text-muted-foreground/50">
                        No carry-forward preservation for this build.
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </Panel>

        {snapshot.runs.length > 0 && (
          <Panel
            title="Recent workflow runs"
            subtitle="Latest workflow activity across all runs for this app."
          >
            <div className="space-y-2">
              {snapshot.runs.slice(0, 6).map((run, i) => (
                <div
                  key={run.run_id || run.id || i}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/36 bg-background/24 px-3 py-2.5 text-sm"
                >
                  <span className="font-medium text-foreground">
                    {run.workflow_name || 'Unnamed workflow'}
                  </span>
                  <span className="text-[12px] text-muted-foreground">
                    {formatDateTimeLabel(run.started_at)}
                  </span>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>
    </WorkspaceLayout>
  )
}
