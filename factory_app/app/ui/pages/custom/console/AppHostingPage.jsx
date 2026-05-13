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
import AppConsoleHero, { formatCompactNumber } from './AppConsoleChrome.jsx'
import { getAppConsoleSnapshot } from './appConsoleDataHelpers.js'
import { formatPercentValue } from './consoleHealthModel.js'
import { buildHostingSections, formatResourceValue } from './hostingConsoleModel.js'
import { getHostingStateLabel, getRuntimeReadinessLabel } from './appConsoleModel.js'
import { useAppConsoleData } from './useAppConsoleData.js'


function HostingDisclosure({ title, items, emptyTitle, emptyDescription, defaultOpen = false }) {
  return (
    <details open={defaultOpen} className="rounded-2xl border border-border/70 bg-background/60">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-foreground">
        <div className="flex items-center justify-between gap-3">
          <span>{title}</span>
          <span className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">{items.length}</span>
        </div>
      </summary>
      <div className="space-y-3 border-t border-border/70 px-4 py-4">
        {items.length > 0 ? items.map((item) => (
          <div key={`${title}-${item.label}`} className="rounded-2xl border border-border/70 bg-card/70 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="font-semibold text-foreground">{item.label}</div>
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">{item.status}</div>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">{item.detail}</div>
          </div>
        )) : (
          <ConsoleInlineEmptyState title={emptyTitle} description={emptyDescription} />
        )}
      </div>
    </details>
  )
}


export default function AppHostingPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const snapshot = useMemo(() => getAppConsoleSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <ConsoleLoadingState label="Loading hosting…" />
  if (error || !data?.summary) return <ConsoleErrorState title="Hosting Unavailable" message={error || 'No hosting summary returned.'} />

  const hosting = buildHostingSections({
    appId,
    appName: snapshot.app?.name || appId,
    status: snapshot.lifecycleState,
    deploymentRecord: snapshot.deploymentRecord,
  })
  const summaryItems = [
    {
      id: 'provider',
      label: 'Provider',
      value: hosting.providerLabel,
      detail: getHostingStateLabel(snapshot.lifecycleState),
    },
    {
      id: 'domains',
      label: 'Domains',
      value: formatCompactNumber(hosting.domainItems.length, '0'),
      detail: hosting.domainItems[0]?.label || 'No domains assigned yet',
    },
    {
      id: 'email',
      label: 'Mailboxes',
      value: formatCompactNumber(hosting.emailItems.length, '0'),
      detail: hosting.emailItems[0]?.label || 'No email configured yet',
    },
    {
      id: 'uptime',
      label: 'Uptime',
      value: formatPercentValue(hosting.uptimePercent),
      detail: hosting.environmentLabel,
    },
    {
      id: 'storage',
      label: 'Storage',
      value: formatResourceValue(hosting.storageGb, ' GB'),
      detail: 'Current managed storage posture',
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
          subtitle="Use Hosting like a provider panel: domains, email, DNS, certificates, and backups for the current app."
          currentSection="hosting"
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.04fr)_minmax(0,0.96fr)]">
          <Panel eyebrow="Provider" title="Hosting control center" subtitle="Keep the provider-level hosting view explicit so domains, email, and storage can be managed from one place.">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Plan</div>
                <div className="mt-2 text-base font-semibold text-foreground">{hosting.planLabel}</div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Environment</div>
                <div className="mt-2 text-base font-semibold text-foreground">{hosting.environmentLabel}</div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Bandwidth</div>
                <div className="mt-2 text-base font-semibold text-foreground">{formatResourceValue(hosting.bandwidthGb, ' GB')}</div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Storage</div>
                <div className="mt-2 text-base font-semibold text-foreground">{formatResourceValue(hosting.storageGb, ' GB')}</div>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Readiness" title="Hosting readiness" subtitle="Keep the hosting posture visible alongside runtime readiness and the app lifecycle.">
            <div className="space-y-3">
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold text-foreground">Hosting posture</div>
                  <StatusPill tone={snapshot.lifecycleState === 'active' ? 'success' : snapshot.lifecycleState === 'deploying' ? 'primary' : 'warning'}>{getHostingStateLabel(snapshot.lifecycleState)}</StatusPill>
                </div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="font-semibold text-foreground">Runtime readiness</div>
                <div className="mt-2 text-muted-foreground">{getRuntimeReadinessLabel(snapshot.summary.workspace?.runtime_readiness)}</div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="font-semibold text-foreground">Uptime</div>
                <div className="mt-2 text-muted-foreground">{formatPercentValue(hosting.uptimePercent)}</div>
              </div>
            </div>
          </Panel>
        </div>

        <Panel eyebrow="Resources" title="Provider resources" subtitle="Expand the sections below to review the resources currently assigned to this app.">
          <div className="space-y-3">
            <HostingDisclosure
              title="Domains"
              items={hosting.domainItems}
              emptyTitle="No domains assigned yet"
              emptyDescription="Hosting will list assigned domains here once managed infrastructure has been configured."
              defaultOpen
            />
            <HostingDisclosure
              title="Email"
              items={hosting.emailItems}
              emptyTitle="No mailboxes configured"
              emptyDescription="Email accounts appear once the app has a managed domain under hosting."
            />
            <HostingDisclosure
              title="DNS & SSL"
              items={hosting.dnsItems}
              emptyTitle="No managed DNS yet"
              emptyDescription="Managed DNS and TLS follow the first domain assignment."
            />
            <HostingDisclosure
              title="Backups & Storage"
              items={hosting.backupItems}
              emptyTitle="No backup posture yet"
              emptyDescription="Storage and backup posture will surface here when hosting is configured."
            />
          </div>
        </Panel>
      </div>
    </AdminWorkspaceLayout>
  )
}