import { Link, useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/ConsoleShared.jsx'
import { AppConsoleHero, formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import { buildSubscriptionMix, getAppConsoleSnapshot, toArray } from './appConsoleDataHelpers.js'
import { useAppConsoleData } from './useAppConsoleData.js'


function BillingActionLink({ to, children, variant = 'primary' }) {
  const className = variant === 'primary'
    ? 'inline-flex h-10 items-center justify-center rounded-[var(--shell-control-radius,1rem)] border border-primary/35 bg-primary px-4 text-sm font-semibold text-primary-foreground transition hover:border-primary/45 hover:bg-primary/92'
    : 'inline-flex h-10 items-center justify-center rounded-[var(--shell-control-radius,1rem)] border border-border/70 bg-background/18 px-4 text-sm font-semibold text-foreground transition hover:border-border/90 hover:bg-card/55'

  return (
    <Link to={to} className={className}>
      {children}
    </Link>
  )
}

export default function AppBillingPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)

  if (loading) return <ConsoleLoadingState label="Loading billing…" />
  if (error || !data) return <ConsoleErrorState title="Billing Unavailable" message={error || 'No billing data returned.'} />

  const snapshot = getAppConsoleSnapshot(appId, data, dataMode)
  const billing = snapshot.billingRecord
  const usersRecord = snapshot.usersRecord
  const subscriptionMix = buildSubscriptionMix(usersRecord)
  const arpu =
    billing?.active_customers
      ? Number(billing.mrr_usd || billing.total_revenue_usd || 0) / Number(billing.active_customers)
      : null
  const failedPayments = Number(billing?.failed_payments || 0)
  const summaryItems = [
    { id: 'revenue', label: 'Revenue', value: formatCurrencyValue(billing?.total_revenue_usd), detail: 'Observed revenue for this app.' },
    { id: 'mrr', label: 'MRR', value: formatCurrencyValue(billing?.mrr_usd), detail: 'Current recurring monthly value.' },
    { id: 'customers', label: 'Active Customers', value: formatCompactNumber(billing?.active_customers, 'Pending'), detail: 'Customers contributing active value.' },
    { id: 'arpu', label: 'ARPU', value: formatCurrencyValue(arpu), detail: 'Average recurring value per active customer.' },
    { id: 'failed', label: 'Failed Payments', value: formatCompactNumber(failedPayments, '0'), detail: 'Known payment exceptions.' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Billing"
          currentSection="Billing"
          subtitle="Track revenue, recurring value, customer billing readiness, and the finance signals that matter without exposing unfinished operations tooling."
          summaryItems={summaryItems}
        />

        <div className="flex flex-wrap gap-3">
          <BillingActionLink to={`/apps/${appId}/hosting`}>Review Hosting</BillingActionLink>
          <BillingActionLink to={`/apps/${appId}/integrations`} variant="secondary">Connect Payments</BillingActionLink>
          <BillingActionLink to={`/apps/${appId}/users`} variant="secondary">View Customer Billing</BillingActionLink>
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <Panel eyebrow="Revenue" title="Revenue status" subtitle="Keep recurring value, customer count, and the next billing signal visible without overloading the page.">
            {billing ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Total revenue</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{formatCurrencyValue(billing.total_revenue_usd)}</div>
                  <div className="mt-2 text-sm text-muted-foreground">Observed top-line contribution from this app.</div>
                </div>
                <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Annualized run rate</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{formatCurrencyValue(billing.arr_usd)}</div>
                  <div className="mt-2 text-sm text-muted-foreground">ARR stays directional until billing systems are fully connected.</div>
                </div>
                <div className="rounded-2xl border border-border bg-card/70 px-4 py-3 sm:col-span-2">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Billing posture</div>
                      <div className="mt-2 font-semibold text-foreground">
                        {billing.active_customers > 0 ? 'Commercial activity is established.' : 'Billing is not active yet.'}
                      </div>
                    </div>
                    <StatusPill tone={billing.active_customers > 0 ? 'success' : 'warning'}>
                      {billing.active_customers > 0 ? 'Revenue active' : 'Pre-revenue'}
                    </StatusPill>
                  </div>
                </div>
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="Billing data appears once payments are connected"
                description="Revenue, MRR, active customers, and payment status become meaningful after this app connects to a billing or subscription flow."
              />
            )}
          </Panel>

          <div className="space-y-6">
            <Panel eyebrow="Plans" title="Plans and subscriptions" subtitle="Use subscription mix to understand what kind of commercial load the app is carrying.">
              {subscriptionMix.length > 0 ? (
                <div className="space-y-3">
                  {subscriptionMix.map((subscription) => (
                    <div key={subscription.label} className="flex items-center justify-between rounded-2xl border border-border bg-card/70 px-4 py-3">
                      <div className="font-semibold text-foreground">{subscription.label}</div>
                      <div className="text-sm text-muted-foreground">{subscription.count} customers</div>
                    </div>
                  ))}
                </div>
              ) : (
                <ConsoleInlineEmptyState
                  title="No plan mix yet"
                  description="Plan and subscription mix will appear once customer plans are tied to app-level users."
                />
              )}
            </Panel>

            <Panel eyebrow="Payments" title="Payments and refunds" subtitle="Flag only the billing signals that usually require real intervention.">
              {failedPayments > 0 || toArray(usersRecord?.support_history).length > 0 ? (
                <div className="space-y-3">
                  {failedPayments > 0 ? (
                    <div className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm">
                      <div className="font-semibold text-foreground">{failedPayments} payment issues need review</div>
                      <div className="mt-2 text-muted-foreground">Use Integrations and Hosting to verify payment connectivity and rollout status.</div>
                    </div>
                  ) : null}
                  {toArray(usersRecord?.support_history).slice(0, 2).map((entry) => (
                    <div key={entry.id} className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                      <div className="font-semibold text-foreground">{entry.label}</div>
                      <div className="mt-2 text-sm text-muted-foreground">{entry.detail}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <ConsoleInlineEmptyState
                  title="No payment issues recorded"
                  description="Refunds, failed payments, and billing interventions will surface here once billing operations are active for this app."
                />
              )}
            </Panel>
          </div>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
