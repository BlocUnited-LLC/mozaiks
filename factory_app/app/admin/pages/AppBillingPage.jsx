import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/ConsoleShared.jsx'
import AppConsoleHero, { formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import { getAppConsoleSnapshot } from './appConsoleDataHelpers.js'
import { useAppConsoleData } from './useAppConsoleData.js'

function paymentReadiness(snapshot) {
  const hasPaymentConnector = snapshot.appConnectors.some((connector) => connector.service === 'stripe' && connector.secret_available)
  if (hasPaymentConnector) return { label: 'Connected', tone: 'success' }
  if (snapshot.lifecycleState === 'active') return { label: 'Ready to connect', tone: 'warning' }
  return { label: 'Pending deployment', tone: 'muted' }
}

export default function AppBillingPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const snapshot = useMemo(() => getAppConsoleSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <ConsoleLoadingState label="Loading app billing..." />
  if (error || !data?.summary) return <ConsoleErrorState title="Billing Unavailable" message={error || 'No billing summary returned.'} />

  const readiness = paymentReadiness(snapshot)
  const summaryItems = [
    { id: 'revenue', label: 'Revenue', value: formatCurrencyValue(0, '$0'), detail: 'No captured payments yet' },
    { id: 'customers', label: 'Customers', value: '0', detail: 'Billing customers' },
    { id: 'failed', label: 'Failed Payments', value: '0', detail: 'No payment failures' },
    { id: 'plans', label: 'Plans', value: formatCompactNumber(0, '0'), detail: 'Pricing plans configured' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          title="Billing"
          subtitle="Configure payment readiness, pricing plans, subscriptions, and revenue reporting for this app."
          actions={(
            <div className="flex flex-wrap gap-2">
              <Link className="inline-flex items-center rounded-full border border-border px-4 py-2 text-sm font-semibold text-foreground transition hover:bg-muted/30" to={`/apps/${appId}/health`}>
                Review Hosting
              </Link>
              <Link className="inline-flex items-center rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90" to={`/apps/${appId}/integrations`}>
                Connect Payments
              </Link>
            </div>
          )}
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel title="Revenue status" subtitle="Revenue reporting starts once payments are connected and the app has active customers.">
            <div className="rounded-2xl border border-border bg-card/55 px-4 py-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-foreground">Payment provider</div>
                  <div className="mt-1 text-sm text-muted-foreground">Connect a provider before plans can collect revenue.</div>
                </div>
                <StatusPill tone={readiness.tone}>{readiness.label}</StatusPill>
              </div>
            </div>
          </Panel>

          <Panel title="Plans and subscriptions" subtitle="Create plans after payment credentials and deployment readiness are in place.">
            <ConsoleInlineEmptyState
              title="No billing plans yet"
              description="Plans and subscriptions will appear here after payment setup is connected for this app."
            />
          </Panel>
        </div>

        <Panel title="Payments and refunds" subtitle="Payment events, refunds, and failed charge follow-up will be listed here.">
          <ConsoleInlineEmptyState
            title="No payment activity yet"
            description="Payments and refunds appear once customers start using a connected plan."
          />
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
