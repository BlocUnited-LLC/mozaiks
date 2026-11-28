import { useEffect, useState } from 'react'
import { InlineEmptyState, ResourceList } from '@mozaiks/chat-ui/ui'
import { ActionButton, LinkButton, Panel, StudioErrorState, StudioLoadingState } from '../../ui/components/StudioShared.jsx'
import { requestConnectors } from './appIntegrationsApi.js'
import { connectorVerdict } from './connectorStatusModel.js'
import { usePortfolioAnalytics } from './useStudioAnalytics.js'
import { DEFAULT_ANALYTICS_PERIOD } from './analyticsModel.js'

const PAGE_SIZE = 10

export default function PortfolioIntegrations() {
  const { data, loading, error, dataMode } = usePortfolioAnalytics(DEFAULT_ANALYTICS_PERIOD)
  const apps = Array.isArray(data?.apps) ? data.apps : []
  const [page, setPage] = useState(0)
  const [results, setResults] = useState({})
  const visible = apps.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const ids = visible.map(app => app.dashboard_app_id).filter(Boolean)
  const key = JSON.stringify(ids)
  useEffect(() => {
    const controller = new AbortController()
    setResults({})
    for (const id of dataMode === 'demo' ? [] : JSON.parse(key)) {
      requestConnectors(id, { signal: controller.signal }).then(data => {
        if (!controller.signal.aborted) setResults(value => ({ ...value, [id]: { data } }))
      }).catch(error => {
        if (!controller.signal.aborted) setResults(value => ({ ...value, [id]: { error: error.message } }))
      })
    }
    return () => controller.abort()
  }, [key, dataMode])
  const status = app => {
    if (!app.dashboard_app_id || dataMode === 'demo') return 'Unavailable'
    const result = results[app.dashboard_app_id]
    if (!result) return 'Loading…'
    if (result.error) return 'Unavailable'
    const connectors = result.data?.connectors
    const declarations = result.data?.declarations
    if (!Array.isArray(connectors) || !Array.isArray(declarations)) return 'Unavailable'
    const attention = connectors.filter(item => connectorVerdict(item).tone !== 'success').length
    return connectors.length + ' connections · ' + declarations.length + ' build requirements' + (attention ? ' · ' + attention + ' need attention' : '')
  }
  const link = app => app.dashboard_app_id ? <LinkButton to={'/apps/' + encodeURIComponent(app.dashboard_app_id) + '/integrations'} size="sm" variant="ghost">{app.name || app.app_id}</LinkButton> : <span>{app.name || app.app_id}</span>
  return <Panel title="Integrations by app" subtitle="Connections and requirements for the apps you manage.">
    {dataMode === 'demo' && <p className="text-sm text-warning">Demo data</p>}
    {loading ? <StudioLoadingState label="Loading app integrations." /> : error ? <StudioErrorState title="App integrations unavailable" message={error} /> : apps.length === 0 ? (
      <InlineEmptyState title="No apps yet" description="App connections appear here after you create an app." />
    ) : <ResourceList items={visible} getItemId={app => app.app_id}
      columns={[{ id: 'app', header: 'App', render: link }, { id: 'status', header: 'Connections', render: status }]}
      renderMobileItem={app => <div className="space-y-2 p-3">{link(app)}<p className="text-sm text-muted-foreground">{status(app)}</p></div>} />}
    {apps.length > PAGE_SIZE && <div className="mt-4 flex justify-end gap-2">
      <ActionButton variant="outline" disabled={page === 0} onClick={() => setPage(value => value - 1)}>Previous apps</ActionButton>
      <ActionButton variant="outline" disabled={(page + 1) * PAGE_SIZE >= apps.length} onClick={() => setPage(value => value + 1)}>Next apps</ActionButton>
    </div>}
  </Panel>
}
