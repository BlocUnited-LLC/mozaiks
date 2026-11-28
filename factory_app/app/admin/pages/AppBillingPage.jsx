import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { PageHeader } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton, Panel, StatusPill, StudioErrorState, StudioInlineEmptyState, StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import { studioFetch } from './studioApi.js'
import SettingsRefinement, { SettingsResume } from './SettingsRefinement.jsx'

const emptyStates = {
  no_build: ['No saved build yet', 'Build this app to see its subscription plans here.'],
  validation_required: ['Build validation required', 'Plans appear after this app’s current build passes validation.'],
  no_catalog: ['No subscription plans configured', 'To offer subscriptions, define this app’s plans in app/config/subscriptions.yaml and validate the build.'],
}

function planPrice(price) {
  if (!price) return 'No recurring price set'
  if (price.display) return price.display
  const amount = new Intl.NumberFormat(undefined, { style: 'currency', currency: price.currency })
    .format(price.amount_cents / 100)
  return `${amount} / ${price.interval}`
}

function SelectedAppBilling({ appId }) {
  const [billing, setBilling] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      const response = await studioFetch(`/api/studio/apps/${encodeURIComponent(appId)}/billing`, { signal: controller.signal })
      if (response.status === 403 || response.status === 404) throw new Error('This app is unavailable to this account.')
      if (!response.ok) throw new Error(`Could not load the selected app’s subscription catalog (HTTP ${response.status}).`)
      const result = await response.json()
      if (result.dashboard_app_id !== appId) throw new Error('The billing response does not match the selected app.')
      if (!controller.signal.aborted) setBilling(result)
    }
    load().catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [appId])

  if (error) return <StudioErrorState title="Subscription unavailable" message={error} />
  if (!billing) return <StudioLoadingState label="Loading subscription plans…" />
  const empty = emptyStates[billing.status]
  return (
    <div className="space-y-6">
      <PageHeader title="Subscription" subtitle={`Subscription plans for ${billing.app_name}.`} />
      <SettingsResume resume={billing.resume} appId={appId} />
      {billing.status === 'edit_in_progress' ? null : empty ? (
        <StudioInlineEmptyState title={empty[0]} description={empty[1]} />
      ) : billing.products.map(product => (
        <Panel key={product.product_id ?? appId} title={product.label} subtitle="Plans in selected build">
          <ul className="divide-y divide-border">
            {product.plans.map(plan => (
              <li key={plan.plan_id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{plan.label}</span>
                    {plan.plan_id === product.default_plan_id && <StatusPill>Default</StatusPill>}
                  </div>
                  {plan.description && <p className="mt-1 text-sm text-muted-foreground">{plan.description}</p>}
                </div>
                <span className="text-sm">{planPrice(plan.price)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ))}
      {billing.settings_refinement_unavailable && <p role="status" className="text-sm text-muted-foreground">{billing.settings_refinement_unavailable}</p>}
      <SettingsRefinement descriptor={billing.settings_refinement} appId={appId} />
      <LinkButton to={`/apps/${encodeURIComponent(appId)}/integrations`}>Manage integrations</LinkButton>
    </div>
  )
}

export default function AppBillingPage() {
  const { appId } = useParams()
  return <WorkspaceLayout><SelectedAppBilling key={appId} appId={appId} /></WorkspaceLayout>
}
