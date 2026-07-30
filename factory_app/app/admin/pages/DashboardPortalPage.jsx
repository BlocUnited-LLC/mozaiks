import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'

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
import { getAppStudioSnapshot, toArray } from './appStudioDataHelpers.js'
import {
  getApprovalStateLabel,
  getAppPrimaryAction,
  getLifecycleGuidance,
  getPlanStateLabel,
  normalizeAppStatus,
} from './appStudioModel.js'
import CarryForwardReportSummary from './CarryForwardReportSummary.jsx'
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

function validationTone(status) {
  if (status === 'passed') return 'success'
  if (status === 'failed') return 'destructive'
  return 'warning'
}

function lifecycleTone(status) {
  if (status === 'current' || status === 'deployed') return 'success'
  if (status === 'draft') return 'default'
  if (status === 'rejected' || status === 'failed') return 'destructive'
  return 'warning'
}

function approvalTone(state) {
  if (state === 'approved') return 'success'
  if (state === 'rejected') return 'destructive'
  if (state === 'pending') return 'warning'
  return 'default'
}

function runTone(status) {
  if (status === 2 || status === 'completed' || status === 'success') return 'success'
  if (status === 'failed' || status === 'error') return 'destructive'
  if (status === 1 || status === 'running') return 'primary'
  return 'default'
}

function runLabel(status) {
  if (status === 2) return 'Completed'
  if (status === 1) return 'Running'
  return String(status || 'Unknown').replace(/_/g, ' ')
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
    fetchDashboardConfig({ scope, appId, signal: controller.signal })
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

function buildHeroSummaryItems(portal, snapshot, data) {
  const build = data?.buildState?.build || {}
  const latestArtifact = snapshot.buildHistory[0] || null
  return [
    {
      id: 'status',
      label: 'App status',
      value: appStatusLabel(snapshot.lifecycleState),
      detail: portal?.label || 'Dashboard',
    },
    {
      id: 'artifacts',
      label: 'Artifacts',
      value: formatCompactNumber(snapshot.buildHistory.length, '0'),
      detail: latestArtifact ? `Latest v${latestArtifact.version_number}` : 'No saved versions',
    },
    {
      id: 'runs',
      label: 'Runs',
      value: formatCompactNumber(snapshot.runs.length, '0'),
      detail: snapshot.runs[0] ? formatRelativeTime(snapshot.runs[0].started_at) : 'No runs yet',
    },
    {
      id: 'approval',
      label: 'Approval',
      value: getApprovalStateLabel(build.approval_state),
      detail: getPlanStateLabel(build.plan_state),
    },
  ]
}

function SummaryStripPanel({ panel, portal, snapshot, data }) {
  return (
    <div>
      <SummaryStrip items={buildHeroSummaryItems(portal, snapshot, data)} />
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

function BuildThreadsPanel({ panel, build, snapshot, appId }) {
  const currentRequest = build.current_request || {}
  const recentRequests = toArray(build.recent_requests)
  const recentRuns = snapshot.runs.slice(0, 4)
  const workflowId = build.initial_compile_workflow || snapshot.workflowNames[0] || 'ValueEngine'
  const chatHref = `/chat?${new URLSearchParams({
    workflow: workflowId,
    mode: 'workflow',
    app_id: appId || '',
  }).toString()}`

  return (
    <Panel title={titleForPanel(panel, 'Build threads')} subtitle={panel.description || 'Current request, saved build prompts, and recent workflow activity.'}>
      {currentRequest.text ? (
        <div className="rounded-lg border border-primary/25 bg-primary/6 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-primary/65">Current request</div>
          <p className="mt-1 text-sm leading-6 text-foreground">{currentRequest.text}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {currentRequest.request_kind ? <StatusPill tone="default">{String(currentRequest.request_kind).replace(/_/g, ' ')}</StatusPill> : null}
            {currentRequest.change_class ? <StatusPill tone="primary">{String(currentRequest.change_class).replace(/_/g, ' ')}</StatusPill> : null}
            {currentRequest.updated_at ? <StatusPill tone="default">{formatRelativeTime(currentRequest.updated_at)}</StatusPill> : null}
          </div>
        </div>
      ) : (
        <StudioInlineEmptyState
          title="No build request saved"
          description="Start or continue a build workflow to attach the current request to this app."
        />
      )}

      {recentRequests.length > 0 ? (
        <div className="mt-4 space-y-2">
          {recentRequests.slice(0, 3).map((request, index) => (
            <div key={`${request.saved_at || index}:${request.text}`} className="rounded-lg border border-border/45 bg-card/35 px-3 py-2.5">
              <div className="text-sm font-medium text-foreground">{request.text}</div>
              <div className="mt-1 text-xs text-muted-foreground">{formatRelativeTime(request.saved_at)}</div>
            </div>
          ))}
        </div>
      ) : null}

      {recentRuns.length > 0 ? (
        <>
          <div className="my-4 border-t border-border/35" />
          <div className="space-y-2">
            {recentRuns.map((run, index) => (
              <div key={run.run_id || run.chat_id || index} className="flex items-center justify-between gap-3 rounded-lg border border-border/45 bg-background/30 px-3 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-foreground">{run.workflow_name || 'Workflow run'}</div>
                  <div className="text-xs text-muted-foreground">{formatRelativeTime(run.started_at)}</div>
                </div>
                {run.status != null ? <StatusPill tone={runTone(run.status)}>{runLabel(run.status)}</StatusPill> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}

      <div className="mt-4">
        <LinkButton to={chatHref} size="sm">Open build workflow</LinkButton>
      </div>
    </Panel>
  )
}

function ArtifactTimelinePanel({ panel, snapshot }) {
  const artifacts = snapshot.buildHistory

  return (
    <Panel title={titleForPanel(panel, 'Artifact timeline')} subtitle={panel.description || 'Saved app bundle versions and validation status.'}>
      {artifacts.length === 0 ? (
        <StudioInlineEmptyState
          title="No artifacts yet"
          description="Artifact versions appear after generation or refinement saves an app bundle."
        />
      ) : (
        <div className="space-y-3">
          {artifacts.slice(0, 6).map((artifact) => {
            const cfReport = artifact?.commit_metadata?.metadata?.carry_forward_report || null
            return (
              <div key={artifact.id || artifact.version_number} className="rounded-lg border border-border/50 bg-card/35 px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground">Build v{artifact.version_number}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{formatDateTimeLabel(artifact.created_at)}</div>
                    {artifact.commit_metadata?.message ? (
                      <div className="mt-1 truncate text-xs text-muted-foreground/75">{artifact.commit_metadata.message}</div>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusPill tone={validationTone(artifact.validation_status)}>{artifact.validation_status || 'pending'}</StatusPill>
                    <StatusPill tone={lifecycleTone(artifact.lifecycle_status)}>{artifact.lifecycle_status || 'draft'}</StatusPill>
                  </div>
                </div>
                {cfReport ? <CarryForwardReportSummary report={cfReport} /> : null}
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

function ApprovalPanel({ panel, build, latestArtifact }) {
  const currentPlan = build.current_plan || {}
  const approvals = toArray(currentPlan.approvals_required)
  const acceptance = toArray(currentPlan.acceptance_criteria)
  const validationStatus = latestArtifact?.validation_status || 'pending'

  return (
    <Panel title={titleForPanel(panel, 'Approvals')} subtitle={panel.description || 'Human review state, artifact validation, and acceptance notes.'}>
      <div className="flex flex-wrap gap-2">
        <StatusPill tone={approvalTone(build.approval_state)}>
          {getApprovalStateLabel(build.approval_state)}
        </StatusPill>
        <StatusPill tone="default">{getPlanStateLabel(build.plan_state)}</StatusPill>
        <StatusPill tone={validationTone(validationStatus)}>{validationStatus}</StatusPill>
      </div>

      {currentPlan.summary ? (
        <p className="mt-4 text-sm leading-6 text-foreground">{currentPlan.summary}</p>
      ) : null}

      {approvals.length > 0 || acceptance.length > 0 ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Approvals</div>
            <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
              {(approvals.length ? approvals : ['No approval blockers recorded.']).slice(0, 4).map((item) => (
                <li key={item} className="rounded-lg border border-border/40 bg-background/28 px-3 py-2">{item}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Acceptance</div>
            <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
              {(acceptance.length ? acceptance : ['Acceptance criteria will appear with the current plan.']).slice(0, 4).map((item) => (
                <li key={item} className="rounded-lg border border-border/40 bg-background/28 px-3 py-2">{item}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <StudioInlineEmptyState
          title="No review checklist yet"
          description="Review tasks appear once a build plan or artifact version has been prepared."
          className="mt-4"
        />
      )}
    </Panel>
  )
}

function workflowHref({ action, panel, build, appId }) {
  if (action?.type === 'route') return routeForApp(action.target, appId)
  if (action?.type === 'external_url') return action.target

  const workflow = action?.type === 'workflow'
    ? action.target
    : build.initial_compile_workflow || panel.workflow_id || 'ValueEngine'
  const params = new URLSearchParams({
    workflow,
    mode: 'workflow',
  })
  if (appId) params.set('app_id', appId)
  if (action?.type === 'workflow_sequence' && action.target) {
    params.set('sequence', action.target)
  }
  if (action?.id) params.set('action_id', action.id)
  return `/chat?${params.toString()}`
}

function WorkflowLauncherPanel({ panel, build, appId }) {
  const actions = toArray(panel.actions)
  const support = build.refinement_support || {}
  const availableModes = Object.entries(support)
    .filter(([, value]) => value?.available)
    .map(([key, value]) => ({ key, workflowId: value.workflow_id }))

  return (
    <Panel title={titleForPanel(panel, 'Workflow launcher')} subtitle={panel.description || 'Open the workflow path declared for this portal.'}>
      {actions.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => {
            const href = workflowHref({ action, panel, build, appId })
            const external = action.type === 'external_url'
            if (external) {
              return (
                <a
                  key={action.id}
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-9 items-center justify-center rounded-lg border border-border bg-card px-3 text-sm font-semibold text-foreground transition hover:bg-muted"
                >
                  {action.label}
                </a>
              )
            }
            return (
              <LinkButton
                key={action.id}
                to={href}
                variant={action.variant === 'primary' ? 'primary' : 'outline'}
                size="sm"
              >
                {action.label}
              </LinkButton>
            )
          })}
        </div>
      ) : (
        <StudioInlineEmptyState
          title="No workflow action declared"
          description="Add a dashboard action to this panel to expose a deterministic launch target."
        />
      )}

      {availableModes.length > 0 ? (
        <>
          <div className="my-4 border-t border-border/35" />
          <div className="grid gap-2 sm:grid-cols-2">
            {availableModes.map((mode) => (
              <div key={mode.key} className="rounded-lg border border-border/45 bg-background/30 px-3 py-2.5">
                <div className="text-sm font-semibold capitalize text-foreground">{mode.key}</div>
                <div className="mt-1 text-xs text-muted-foreground">{mode.workflowId}</div>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </Panel>
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

function DashboardPanelRenderer({ panel, portal, surface, snapshot, data, appId }) {
  const build = data?.buildState?.build || {}
  const latestArtifact = snapshot.buildHistory[0] || null

  switch (panel.type) {
    case 'summary_strip':
    case 'kpi_grid':
      return <SummaryStripPanel panel={panel} portal={portal} snapshot={snapshot} data={data} />
    case 'next_step':
      return <NextStepPanel panel={panel} snapshot={snapshot} />
    case 'portal_link_grid':
      return <PortalLinkGridPanel panel={panel} surface={surface} currentPortal={portal} appId={appId} />
    case 'build_threads':
      return <BuildThreadsPanel panel={panel} build={build} snapshot={snapshot} appId={appId} />
    case 'artifact_timeline':
      return <ArtifactTimelinePanel panel={panel} snapshot={snapshot} />
    case 'approval_queue':
    case 'approval_votes':
      return <ApprovalPanel panel={panel} build={build} latestArtifact={latestArtifact} />
    case 'workflow_launcher':
      return <WorkflowLauncherPanel panel={panel} build={build} appId={appId} />
    default:
      return <GenericPanelFallback panel={panel} />
  }
}

function isWidePanel(type) {
  return ['summary_strip', 'kpi_grid', 'portal_link_grid', 'artifact_timeline'].includes(type)
}

export default function DashboardPortalPage() {
  const params = useParams()
  const location = useLocation()
  const appId = params.appId ? decodePathSegment(params.appId) : null
  const scope = appId ? 'app' : 'workspace'
  const manifest = useDashboardPortal(scope, appId, location.pathname)
  const { data, loading: appLoading, error: appError, dataMode, refresh } = useAppStudioData(appId || 'workspace-app')
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
          summaryItems={buildHeroSummaryItems(portal, snapshot, data)}
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
