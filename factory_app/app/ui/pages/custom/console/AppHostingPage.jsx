import { useMemo } from 'react'
import { useParams } from 'react-router-dom'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import AppConsoleHero, { formatCompactNumber, formatDateTimeLabel } from './AppConsoleChrome.jsx'
import { getAppConsoleSnapshot } from './appConsoleDataHelpers.js'
import {
  getDeploymentHealthLabel,
  getHostingStateLabel,
  getRuntimeReadinessLabel,
} from './appConsoleModel.js'
import { useAppConsoleData } from './useAppConsoleData.js'


export default function AppHostingPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const snapshot = useMemo(() => getAppConsoleSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <ConsoleLoadingState label="Loading hosting…" />
  if (error || !data?.summary) return <ConsoleErrorState title="Hosting Unavailable" message={error || 'No hosting summary returned.'} />

  const latestArtifact = snapshot.buildHistory[0] || null
  const domains = Array.isArray(snapshot.deploymentRecord?.domains) ? snapshot.deploymentRecord.domains : []
  const summaryItems = [
    {
      id: 'hosting',
      label: 'Hosting Posture',
      value: getDeploymentHealthLabel(snapshot.lifecycleState),
      detail: getHostingStateLabel(snapshot.lifecycleState),
    },
    {
      id: 'latest',
      label: 'Latest Build',
      value: latestArtifact ? `v${latestArtifact.version_number}` : 'Pending',
      detail: latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'Awaiting first build version',
    },
    {
      id: 'domains',
      label: 'Assigned Domains',
      value: formatCompactNumber(domains.length, '0'),
      detail: domains[0] || 'No domains assigned yet',
    },
    {
      id: 'runtime',
      label: 'Runtime Readiness',
      value: getRuntimeReadinessLabel(snapshot.summary.workspace?.runtime_readiness),
      detail: snapshot.summary.workspace?.entry_point || 'No runtime entry point configured',
    },
  ]

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title="Hosting"
          subtitle="Review managed hosting readiness, deployment history, and environment posture without leaving the app console."
          currentSection="hosting"
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.04fr)_minmax(0,0.96fr)]">
          <Panel eyebrow="Trail" title="Recent hosting trail" subtitle="Keep the latest saved versions visible so hosting decisions stay tied to actual artifacts.">
            <div className="space-y-3">
              {snapshot.buildHistory.length > 0 ? snapshot.buildHistory.map((version) => (
                <div key={version.id || version.version_number} className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-foreground">Build version {version.version_number}</div>
                      <div className="mt-1 text-sm text-muted-foreground">{formatDateTimeLabel(version.created_at)}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <StatusPill tone={version.lifecycle_status === 'deployed' ? 'success' : 'primary'}>
                        {version.lifecycle_status || 'draft'}
                      </StatusPill>
                      <StatusPill tone={version.validation_status === 'passed' ? 'success' : 'warning'}>
                        {version.validation_status || 'pending'}
                      </StatusPill>
                    </div>
                  </div>
                </div>
              )) : (
                <div className="rounded-[1.5rem] border border-dashed border-border/70 bg-background/55 px-4 py-6 text-sm text-muted-foreground">
                  No hosting trail is available yet.
                </div>
              )}
            </div>
          </Panel>

          <Panel eyebrow="Domains" title="Assigned domains" subtitle="Managed domains appear here when hosting has been mapped to a stable endpoint.">
            {domains.length > 0 ? (
              <div className="space-y-3">
                {domains.map((domain) => (
                  <div key={domain} className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm text-foreground">
                    {domain}
                  </div>
                ))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No domains assigned yet"
                description="Hosting will list assigned domains here once managed infrastructure has been configured."
              />
            )}
          </Panel>
        </div>

        <Panel eyebrow="Readiness" title="Environment readiness" subtitle="Keep the target environments explicit so operators know what is ready now and what still needs setup.">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Production</div>
              <div className="mt-2 text-base font-semibold text-foreground">{snapshot.lifecycleState === 'active' ? 'Live' : snapshot.lifecycleState === 'deploying' ? 'Provisioning' : 'Pending release'}</div>
              <div className="mt-1 text-muted-foreground">Primary hosted environment for customer traffic.</div>
            </div>
            <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Staging</div>
              <div className="mt-2 text-base font-semibold text-foreground">{['review', 'configuring', 'deploying', 'active'].includes(snapshot.lifecycleState) ? 'Ready for checks' : 'Awaiting build output'}</div>
              <div className="mt-1 text-muted-foreground">Pre-release environment for validation and operator review.</div>
            </div>
            <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Release Guard</div>
              <div className="mt-2 text-base font-semibold text-foreground">{getRuntimeReadinessLabel(snapshot.summary.workspace?.runtime_readiness)}</div>
              <div className="mt-1 text-muted-foreground">Current runtime gate before promotion into production.</div>
            </div>
          </div>
        </Panel>
      </div>
    </AdminWorkspaceLayout>
  )
}