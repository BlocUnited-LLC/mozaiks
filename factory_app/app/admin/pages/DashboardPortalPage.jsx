import { Suspense, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'

import { componentRegistry } from '@mozaiks/chat-ui'
import { SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, {
  formatCompactNumber,
  formatDateTimeLabel,
} from './AppStudioChrome.jsx'
import AppProjectPanel from './AppProjectPanel.jsx'
import { getAppStudioSnapshot, toArray } from './appStudioDataHelpers.js'
import {
  getAppPrimaryAction,
  getLifecycleGuidance,
  normalizeAppStatus,
} from './appStudioModel.js'
import { fetchDashboardConfig, getDashboardSurface } from './dashboardRoutes.js'
import { useAppStudioData } from './useAppStudioData.js'

function decodePathSegment(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function splitPath(value) {
  return String(value || '')
    .split('?')[0]
    .split('#')[0]
    .replace(/\/+$/, '')
    .split('/')
    .filter(Boolean)
}

function routePatternMatches(pattern, pathname) {
  const patternParts = splitPath(pattern)
  const pathParts = splitPath(pathname)
  if (patternParts.length !== pathParts.length) return false
  return patternParts.every((part, index) => part.startsWith(':') || part === pathParts[index])
}

function routeForApp(route, appId) {
  if (!route) return null
  return String(route).replace(':appId', encodeURIComponent(appId || ''))
}

function titleForPanel(panel, fallback) {
  return panel?.title || fallback || String(panel?.type || 'Panel').replace(/_/g, ' ')
}

function formatRelativeTime(value) {
  if (!value) return 'Not recorded'
  try {
    const timestamp = new Date(value).getTime()
    const diff = Date.now() - timestamp
    if (!Number.isFinite(diff)) return formatDateTimeLabel(value)
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    if (days < 7) return `${days}d ago`
    return formatDateTimeLabel(value)
  } catch {
    return formatDateTimeLabel(value)
  }
}

function appStatusLabel(status) {
  const normalized = normalizeAppStatus(status)
  return normalized === 'draft' ? 'Draft' : normalized.replace(/_/g, ' ')
}

function appStatusTone(status) {
  const normalized = normalizeAppStatus(status)
  if (['active', 'hosted'].includes(normalized)) return 'success'
  if (['failed', 'needs_revision'].includes(normalized)) return 'destructive'
  if (['review', 'configuring', 'deploying'].includes(normalized)) return 'warning'
  return 'default'
}

function useDashboardPortal(scope, appId, pathname) {
  const [state, setState] = useState({
    loading: true,
    error: null,
    surface: null,
    portal: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    setState({ loading: true, error: null, surface: null, portal: null })
    fetchDashboardConfig({ scope, signal: controller.signal })
      .then((payload) => {
        const surface = getDashboardSurface(payload, scope)
        const portals = Array.isArray(surface?.portals) ? surface.portals : []
        const portal = portals.find((item) => (
          item?.enabled !== false &&
          routePatternMatches(item.route, pathname)
        )) || null
        setState({ loading: false, error: null, surface, portal })
      })
      .catch((err) => {
        if (err?.name === 'AbortError') return
        setState({
          loading: false,
          error: err instanceof Error ? err.message : 'Dashboard portal could not be loaded.',
          surface: null,
          portal: null,
        })
      })
    return () => controller.abort()
  }, [appId, pathname, scope])

  return state
}

function buildSummaryItems(portal, snapshot) {
  const latestVersion = snapshot.buildHistory[0] || null
  return [
    {
      id: 'status',
      label: 'App status',
      value: appStatusLabel(snapshot.lifecycleState),
      detail: portal?.label || 'Dashboard',
    },
    {
      id: 'versions',
      label: 'App versions',
      value: formatCompactNumber(snapshot.buildHistory.length, '0'),
      detail: latestVersion ? `Latest: version ${latestVersion.version_number}` : 'No saved versions',
    },
    {
      id: 'runs',
      label: 'Runs',
      value: formatCompactNumber(snapshot.runs.length, '0'),
      detail: snapshot.runs[0] ? formatRelativeTime(snapshot.runs[0].started_at) : 'No runs yet',
    },
  ]
}

function SummaryStripPanel({ panel, portal, snapshot }) {
  return (
    <div>
      <SummaryStrip items={buildSummaryItems(portal, snapshot)} />
      {panel.description ? (
        <p className="mt-2 px-1 text-xs leading-5 text-muted-foreground">{panel.description}</p>
      ) : null}
    </div>
  )
}

function NextStepPanel({ panel, snapshot }) {
  const lifecycle = normalizeAppStatus(snapshot.lifecycleState)
  const guidance = getLifecycleGuidance(lifecycle)
  const action = getAppPrimaryAction(snapshot.app)

  return (
    <Panel title={titleForPanel(panel, 'Next step')} subtitle={panel.description || 'Recommended next action for this app.'}>
      <StudioInlineEmptyState
        title={guidance || 'No immediate action'}
        description={action?.label ? 'Open the recommended surface when you are ready to continue.' : 'The app has no required next action at this stage.'}
      />
      {action?.href && action?.label ? (
        <div className="mt-4">
          <LinkButton to={action.href} size="sm">{action.label}</LinkButton>
        </div>
      ) : null}
    </Panel>
  )
}

function PortalLinkGridPanel({ panel, surface, currentPortal, appId }) {
  const portals = toArray(surface?.portals).filter((portal) => portal.enabled !== false)
  return (
    <Panel title={titleForPanel(panel, 'Portal map')} subtitle={panel.description || 'Enabled dashboard portals declared for this surface.'}>
      <div className="grid gap-3 sm:grid-cols-2">
        {portals.map((portal) => {
          const current = portal.id === currentPortal?.id
          const href = routeForApp(portal.route, appId)
          return (
            <Link
              key={portal.id}
              to={href || '#'}
              className={[
                'rounded-lg border px-4 py-3 transition',
                current
                  ? 'border-primary/35 bg-primary/8'
                  : 'border-border/50 bg-card/45 hover:border-primary/30 hover:bg-primary/5',
              ].join(' ')}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-semibold text-foreground">{portal.label}</span>
                <StatusPill tone={current ? 'primary' : 'default'}>{current ? 'Current' : 'Open'}</StatusPill>
              </div>
              {portal.description ? (
                <p className="mt-2 text-xs leading-5 text-muted-foreground">{portal.description}</p>
              ) : null}
            </Link>
          )
        })}
      </div>
    </Panel>
  )
}

/**
 * Host extension point: a `custom_component` panel renders the component a
 * host registered under `panel.component` (see app-dashboard-contract.md).
 */
function CustomComponentPanel({ panel, appId, data }) {
  const Component = panel.component ? componentRegistry.getComponent(panel.component) : null
  if (!Component) return <GenericPanelFallback panel={panel} />
  return (
    <Suspense fallback={<StudioLoadingState label={`Loading ${titleForPanel(panel)}...`} />}>
      <Component
        panel={panel}
        appId={appId}
        app={data?.registeredApp || data?.summary?.app || null}
        buildRegistryId={data?.buildRegistryId || null}
      />
    </Suspense>
  )
}

function GenericPanelFallback({ panel }) {
  return (
    <Panel title={titleForPanel(panel)} subtitle={panel.description || `Declared dashboard panel type: ${panel.type}.`}>
      <StudioInlineEmptyState
        title="Generic renderer pending"
        description="This dashboard panel is valid, but it needs a reusable renderer before it can show live data."
      />
    </Panel>
  )
}

function DashboardPanelRenderer({ panel, portal, surface, snapshot, data, dataMode, appId }) {
  switch (panel.type) {
    case 'summary_strip':
    case 'kpi_grid':
      return <SummaryStripPanel panel={panel} portal={portal} snapshot={snapshot} />
    case 'next_step':
      return <NextStepPanel panel={panel} snapshot={snapshot} />
    case 'portal_link_grid':
      return <PortalLinkGridPanel panel={panel} surface={surface} currentPortal={portal} appId={appId} />
    case 'build_project':
      return (
        <AppProjectPanel
          appId={appId}
          app={data?.registeredApp || snapshot.app}
          buildRegistryId={data?.buildRegistryId || null}
          dataMode={dataMode}
          demoVersions={snapshot.buildHistory}
        />
      )
    case 'custom_component':
      return <CustomComponentPanel panel={panel} appId={appId} data={data} />
    default:
      return <GenericPanelFallback panel={panel} />
  }
}

function isWidePanel(type) {
  return ['summary_strip', 'kpi_grid', 'portal_link_grid', 'build_project', 'custom_component'].includes(type)
}

export default function DashboardPortalPage({ targetAppId, buildRegistryId } = {}) {
  const params = useParams()
  const location = useLocation()
  const appId = params.appId ? decodePathSegment(params.appId) : null
  const scope = appId ? 'app' : 'workspace'
  const manifest = useDashboardPortal(scope, appId, location.pathname)
  const { data, loading: appLoading, error: appError, dataMode, refresh } = useAppStudioData(targetAppId || appId || 'workspace-app', buildRegistryId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (manifest.loading || appLoading) {
    return <StudioLoadingState label="Loading dashboard portal..." />
  }
  if (manifest.error) {
    return <StudioErrorState title="Dashboard Portal Unavailable" message={manifest.error} />
  }
  if (!manifest.portal) {
    return <StudioErrorState title="Dashboard Portal Not Registered" message="The current route is not represented by an enabled dashboard portal." />
  }
  if (appError || !data?.summary) {
    return <StudioErrorState title={`${manifest.portal.label} Unavailable`} message={appError || 'No app summary returned.'} />
  }

  const portal = manifest.portal
  const panels = toArray(portal.panels).slice().sort((left, right) => (
    Number(left.order || 0) - Number(right.order || 0) || String(left.id).localeCompare(String(right.id))
  ))

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title={portal.label}
          subtitle={portal.description || `${portal.label} portal for this app.`}
          actions={[{ id: 'refresh', label: 'Refresh', variant: 'outline' }]}
          onAction={(id) => id === 'refresh' && refresh()}
        />

        {portal.capabilities?.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {portal.capabilities.map((capability) => (
              <StatusPill key={capability} tone="default">{String(capability).replace(/_/g, ' ')}</StatusPill>
            ))}
          </div>
        ) : null}

        {panels.length === 0 ? (
          <Panel title="No panels declared" subtitle="This portal is enabled but does not declare dashboard panels.">
            <StudioInlineEmptyState
              title="Portal content pending"
              description="Add dashboard panels to the manifest to render this portal with live Studio data."
            />
          </Panel>
        ) : (
          <div className="grid gap-5 lg:grid-cols-2">
            {panels.map((panel) => (
              <div key={panel.id} className={isWidePanel(panel.type) ? 'lg:col-span-2' : 'min-w-0'}>
                <DashboardPanelRenderer
                  panel={panel}
                  portal={portal}
                  surface={manifest.surface}
                  snapshot={snapshot}
                  data={data}
                  dataMode={dataMode}
                  appId={appId}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </WorkspaceLayout>
  )
}
