import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { Alert } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  LinkButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero, {
  formatCompactNumber,
  formatCurrencyValue,
  formatDateTimeLabel,
} from './AppStudioChrome.jsx'
import CarryForwardReportPanel from './CarryForwardReportPanel.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import {
  getApprovalStateLabel,
  getAppPrimaryAction,
  getLifecycleGuidance,
  getPlanStateLabel,
  normalizeAppStatus,
} from './appStudioModel.js'
import { API_BASE } from './studioApi.js'
import { useAppStudioData } from './useAppStudioData.js'


// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatRelativeTime(iso) {
  if (!iso) return null
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    if (days < 7) return `${days}d ago`
    return formatDateTimeLabel(iso)
  } catch {
    return formatDateTimeLabel(iso)
  }
}

function validationTone(value) {
  if (value === 'passed') return 'success'
  if (value === 'failed') return 'destructive'
  return 'warning'
}

function approvalTone(state) {
  if (state === 'approved') return 'success'
  if (state === 'rejected') return 'destructive'
  if (state === 'pending') return 'warning'
  return 'default'
}

function runStatusTone(status) {
  if (status === 2) return 'success'
  if (status === 1) return 'primary'
  return 'default'
}

function runStatusLabel(status) {
  if (status === 2) return 'Completed'
  if (status === 1) return 'Running'
  return 'Unknown'
}

function contextReadinessTone(status) {
  if (status === 'ready' || status === 'succeeded') return 'success'
  if (status === 'failed') return 'destructive'
  if (status === 'running' || status === 'queued') return 'primary'
  if (status === 'stale' || status === 'degraded') return 'warning'
  return 'default'
}

function contextReadinessLabel(status) {
  if (status === 'ready') return 'Ready'
  if (status === 'running') return 'Indexing'
  if (status === 'queued') return 'Queued'
  if (status === 'stale') return 'Stale'
  if (status === 'degraded') return 'Degraded'
  if (status === 'failed') return 'Failed'
  return 'Missing'
}

function readNumber(...values) {
  for (const value of values) {
    if (value == null || value === '') continue
    const num = Number(value)
    if (Number.isFinite(num)) return num
  }
  return null
}

function formatPercentLabel(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 1)}%`
}

function formatMarginLabel(revenue, cost) {
  if (revenue == null && cost == null) return 'Pending'
  return formatCurrencyValue(Number(revenue || 0) - Number(cost || 0), '$0.00')
}

function buildDashboardMetrics(snapshot, totalCost, totalRuns) {
  const billing = snapshot.billingRecord || {}
  const users = snapshot.usersRecord || {}
  const summary = snapshot.summary || {}
  const app = snapshot.app || {}
  const usageTotals = snapshot.usage?.totals || {}

  const revenue = readNumber(
    billing.total_revenue_usd,
    summary.financials?.total_revenue_usd,
    summary.revenue?.total_revenue_usd,
    app.total_revenue_usd,
  )
  const mrr = readNumber(
    billing.mrr_usd,
    summary.financials?.mrr_usd,
    summary.revenue?.mrr_usd,
    app.mrr_usd,
  )
  const activeUsers = readNumber(
    users.active_users,
    summary.access?.active_users,
    summary.users?.active_users,
    snapshot.stats?.active_users,
    app.active_users,
  )
  const totalUsers = readNumber(
    users.total_users,
    summary.access?.total_users,
    summary.users?.total_users,
    snapshot.stats?.total_users,
    app.total_users,
  )
  const llmCalls = readNumber(usageTotals.llm_calls, snapshot.usageRecord?.llm_calls)
  const margin = revenue == null && totalCost == null ? null : Number(revenue || 0) - Number(totalCost || 0)
  const marginRate = revenue && revenue > 0 ? (margin / revenue) * 100 : null

  return {
    revenue,
    mrr,
    cost: totalCost,
    margin,
    marginRate,
    activeUsers,
    totalUsers,
    chats: totalRuns,
    llmCalls,
  }
}


// ─── Next Step Callout ────────────────────────────────────────────────────────

function NextStepCallout({ nextStep, action }) {
  if (!nextStep && !action) return null
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-primary/25 bg-primary/6 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="text-[10px] font-semibold uppercase tracking-widest text-primary/55">Next step</div>
        <p className="mt-1 text-sm leading-relaxed text-foreground">{nextStep}</p>
      </div>
      {action?.href && action?.label ? (
        <LinkButton to={action.href} variant="secondary" size="sm" className="shrink-0 font-semibold">
          {action.label}
        </LinkButton>
      ) : null}
    </div>
  )
}


// ─── KPI Icons ────────────────────────────────────────────────────────────────

function IconRevenue({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6" />
    </svg>
  )
}

function IconCost({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  )
}

function IconMargin({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
      <polyline points="16 7 22 7 22 13" />
    </svg>
  )
}

function IconUsers({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  )
}

function IconChats({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
    </svg>
  )
}

const KPI_ICONS = {
  revenue: IconRevenue,
  cost: IconCost,
  margin: IconMargin,
  users: IconUsers,
  chats: IconChats,
}


// ─── KPI Cards ────────────────────────────────────────────────────────────────

function KpiCard({ id, label, value, detail }) {
  const Icon = KPI_ICONS[id]
  return (
    <div className="flex flex-col gap-2 overflow-hidden rounded-xl border border-border/50 bg-card/70 px-5 py-4 shadow-sm shadow-black/4">
      <div className="flex items-center gap-1.5 text-muted-foreground/65">
        {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
        <span className="text-[11px] font-semibold uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-2xl font-bold leading-none tracking-tight text-foreground">{value}</div>
      {detail ? (
        <div className="text-xs text-muted-foreground/60">{detail}</div>
      ) : (
        <div className="h-4" />
      )}
    </div>
  )
}

function KpiGrid({ items }) {
  if (!items.length) return null
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item) => (
        <KpiCard key={item.id} {...item} />
      ))}
    </div>
  )
}


// ─── Sub-components ──────────────────────────────────────────────────────────

function BuildStatusPanel({ build, latestArtifact, isApprovalPending, appId }) {
  const hasRequest = !!build.current_request?.text
  const approvalState = build.approval_state
  const planState = build.plan_state
  const hasContent = hasRequest || latestArtifact || approvalState || planState

  return (
    <Panel
      title={isApprovalPending ? 'Approval required' : 'Build status'}
      eyebrow={isApprovalPending ? 'Action needed' : null}
      subtitle={isApprovalPending
        ? 'Review the current plan and respond before the build can progress.'
        : 'Current request and artifact state.'}
    >
      {hasRequest ? (
        <p className="text-sm leading-6 text-foreground">{build.current_request.text}</p>
      ) : latestArtifact ? (
        <div className="flex items-start justify-between gap-3">
          <div>
            <LinkButton
              to={`/apps/${encodeURIComponent(appId)}/activity`}
              variant="ghost"
              size="sm"
              className="p-0 font-semibold text-foreground hover:text-primary"
            >
              Build v{latestArtifact.version_number}
            </LinkButton>
            <div className="mt-0.5 text-sm text-muted-foreground">
              {formatRelativeTime(latestArtifact.created_at)}
            </div>
          </div>
          <StatusPill tone={validationTone(latestArtifact.validation_status)}>
            {latestArtifact.validation_status || 'pending'}
          </StatusPill>
        </div>
      ) : !hasContent ? (
        <p className="text-sm text-muted-foreground">
          No build sessions yet. Start a build to track artifact history and approval state here.
        </p>
      ) : null}

      {(approvalState || planState) && (
        <>
          <div className="my-4 border-t border-border/30" />
          <div className="flex flex-wrap items-center gap-2">
            {approvalState && (
              <StatusPill tone={approvalTone(approvalState)}>
                {getApprovalStateLabel(approvalState)}
              </StatusPill>
            )}
            {planState && (
              <StatusPill tone="default">{getPlanStateLabel(planState)}</StatusPill>
            )}
            {latestArtifact && hasRequest && (
              <StatusPill tone={validationTone(latestArtifact.validation_status)}>
                {latestArtifact.validation_status || 'pending'}
              </StatusPill>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}

function ActivityPanel({ snapshot, latestRun, totalRuns, appId }) {
  const latestWorkflow = latestRun?.workflow_name || null

  return (
    <Panel title="Activity" subtitle="Runtime usage and recent runs.">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Chats</span>
          <span className="text-2xl font-bold tracking-tight text-foreground">
            {formatCompactNumber(totalRuns, '0')}
          </span>
          <span className="text-xs text-muted-foreground/60">
            {latestRun ? formatRelativeTime(latestRun.started_at) : 'No runs yet'}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Last workflow</span>
          <span className="truncate text-base font-semibold text-foreground">{latestWorkflow || '—'}</span>
          <span className="text-xs text-muted-foreground/60">
            {latestRun ? formatRelativeTime(latestRun.started_at) : 'No runs yet'}
          </span>
        </div>
      </div>

      <div className="my-4 border-t border-border/30" />

      {latestRun ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Latest run</div>
              <div className="mt-0.5 truncate text-sm font-medium text-foreground">
                {latestRun.workflow_name || 'Workflow run'}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground/60">
                {latestRun.user_id || 'Operator'} · {formatRelativeTime(latestRun.started_at)}
              </div>
            </div>
            {latestRun.status != null && (
              <StatusPill tone={runStatusTone(latestRun.status)}>
                {runStatusLabel(latestRun.status)}
              </StatusPill>
            )}
          </div>
        </>
      ) : (
        <StudioInlineEmptyState
          title="No runs yet"
          description="Runs appear once the app is live and receiving workflow traffic."
        />
      )}

      {snapshot.buildHistory.length > 0 && (
        <>
          <div className="my-4 border-t border-border/30" />
          <LinkButton
            to={`/apps/${encodeURIComponent(appId)}/activity`}
            variant="outline"
            size="sm"
          >
            View build history →
          </LinkButton>
        </>
      )}
    </Panel>
  )
}

function AppIntelligencePanel({ context, appId }) {
  const [validationRunning, setValidationRunning] = useState(false)
  const [validationResult, setValidationResult] = useState(null)
  const [validationError, setValidationError] = useState(null)
  const readiness = context?.context_readiness || {}
  const job = context?.index_job || null
  const graph = context?.context_graph_status || {}
  const appIntelligence = job?.app_intelligence || {}
  const frameworkDetection = appIntelligence.framework_detection || {}
  const status = readiness.status || job?.status || (graph.available ? 'ready' : 'missing')
  const phase = job?.phases?.find((item) => item.id === job.current_phase)
  const commands = frameworkDetection.validation_commands || []
  const frameworks = frameworkDetection.frameworks || []
  const warnings = readiness.warnings || job?.warnings || context?.warnings || []
  const canRunValidation = ['ready', 'degraded', 'succeeded'].includes(status) || graph.available
  const validationCommands = validationResult?.command_results || []
  const validationFallbacks = validationResult?.fallback_checks || []

  async function handleRunValidation() {
    setValidationRunning(true)
    setValidationError(null)
    try {
      const response = await fetch(`${API_BASE}/api/studio/apps/${encodeURIComponent(appId)}/context/validation/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_execution: true }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(body?.detail || 'Validation could not be started.')
      }
      setValidationResult(body.validation || null)
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : 'Validation could not be started.')
    } finally {
      setValidationRunning(false)
    }
  }

  return (
    <Panel
      title="App intelligence"
      subtitle="Source context available to build and refinement agents."
      eyebrow={job?.updated_at ? `Updated ${formatRelativeTime(job.updated_at)}` : null}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={contextReadinessTone(status)}>{contextReadinessLabel(status)}</StatusPill>
        {readiness.primary_framework_label || frameworkDetection.primary_framework_label ? (
          <StatusPill tone="default">{readiness.primary_framework_label || frameworkDetection.primary_framework_label}</StatusPill>
        ) : null}
        {frameworkDetection.monorepo ? <StatusPill tone="default">Monorepo</StatusPill> : null}
      </div>

      {job?.status === 'running' || job?.status === 'queued' ? (
        <div className="mt-4 space-y-2">
          <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>{phase?.message || phase?.label || 'Preparing source context'}</span>
            <span>{formatPercentLabel(job.progress_percent || 0, '0%')}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${Math.max(3, Math.min(100, Number(job.progress_percent || 0)))}%` }}
            />
          </div>
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Files</div>
          <div className="mt-1 text-xl font-bold text-foreground">
            {formatCompactNumber(readiness.indexed_file_count ?? appIntelligence.indexed_file_count ?? 0, '0')}
          </div>
        </div>
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Graph</div>
          <div className="mt-1 text-xl font-bold text-foreground">
            {formatCompactNumber(graph.node_count || 0, '0')} / {formatCompactNumber(graph.edge_count || 0, '0')}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground/60">nodes / edges</div>
        </div>
      </div>

      {frameworks.length > 0 ? (
        <>
          <div className="my-4 border-t border-border/30" />
          <div className="flex flex-wrap gap-2">
            {frameworks.slice(0, 6).map((framework) => (
              <StatusPill key={framework.framework_id} tone="default">{framework.label}</StatusPill>
            ))}
          </div>
        </>
      ) : null}

      {commands.length > 0 ? (
        <>
          <div className="my-4 border-t border-border/30" />
          <div className="space-y-2">
            {commands.slice(0, 3).map((command) => (
              <div key={`${command.kind}:${command.command}:${command.working_directory}`} className="flex items-center justify-between gap-3 rounded-lg border border-border/45 px-3 py-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/65">{command.kind}</span>
                <code className="truncate text-xs text-foreground">{command.command}</code>
              </div>
            ))}
          </div>
        </>
      ) : null}

      <div className="my-4 border-t border-border/30" />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/65">Validation</div>
          <div className="mt-0.5 text-xs text-muted-foreground/70">
            {validationResult
              ? `${validationCommands.length} commands · ${validationFallbacks.length} fallback checks`
              : commands.length > 0
                ? `${commands.length} detected commands`
                : 'Fallback checks available'}
          </div>
        </div>
        <ActionButton
          variant="outline"
          size="sm"
          disabled={!canRunValidation || validationRunning}
          onClick={handleRunValidation}
        >
          {validationRunning ? 'Running...' : 'Run checks'}
        </ActionButton>
      </div>

      {validationError ? (
        <p className="mt-3 text-xs leading-5 text-destructive">{validationError}</p>
      ) : null}

      {validationResult ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={validationTone(validationResult.validation_status)}>
              {validationResult.validation_status || 'skipped'}
            </StatusPill>
            {validationResult.execution_mode ? <StatusPill tone="default">{validationResult.execution_mode}</StatusPill> : null}
          </div>
          {validationCommands.slice(0, 3).map((command) => (
            <div key={`${command.kind}:${command.command}:${command.duration_ms}`} className="rounded-lg border border-border/45 px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-xs font-medium text-foreground">{command.command}</span>
                <StatusPill tone={validationTone(command.status)}>{command.status}</StatusPill>
              </div>
              {command.reason ? <div className="mt-1 text-xs text-muted-foreground/65">{command.reason}</div> : null}
            </div>
          ))}
          {validationCommands.length === 0 && validationFallbacks.slice(0, 2).map((check) => (
            <div key={check.name} className="rounded-lg border border-border/45 px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-xs font-medium text-foreground">{check.name}</span>
                <StatusPill tone={validationTone(check.status)}>{check.status}</StatusPill>
              </div>
              <div className="mt-1 text-xs text-muted-foreground/65">{check.reason}</div>
            </div>
          ))}
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <p className="mt-4 text-xs leading-5 text-warning">{warnings.slice(0, 2).join(' · ')}</p>
      ) : null}
    </Panel>
  )
}


// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AppOverviewPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode, refresh } = useAppStudioData(appId)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])

  if (loading) return <StudioLoadingState label="Loading App Overview…" />
  if (error || !data?.summary) return <StudioErrorState title="App Overview Unavailable" message={error || 'No summary returned.'} />

  const build = data.buildState?.build || {}
  const latestArtifact = snapshot.buildHistory[0] || null
  const cfReport = latestArtifact?.commit_metadata?.metadata?.carry_forward_report
    || latestArtifact?.metadata?.carry_forward_report
    || null
  const latestRun = snapshot.runs[0] || null
  const totalCost = Number(snapshot.usage?.totals?.estimated_cost_usd || 0)
  const totalRuns = Number(snapshot.stats?.tracked_chats || 0)

  const lifecycle = normalizeAppStatus(snapshot.lifecycleState)
  const isDraft = lifecycle === 'draft' && !latestArtifact && !build.current_request?.text
  const isApprovalPending = build.approval_state === 'pending'
  const isApprovalRejected = build.approval_state === 'rejected'

  const primaryAction = getAppPrimaryAction(snapshot.app)
  const nextStep = getLifecycleGuidance(lifecycle)
  const dashboardMetrics = buildDashboardMetrics(snapshot, totalCost, totalRuns)

  const kpiItems = [
    {
      id: 'revenue',
      label: 'Revenue',
      value: formatCurrencyValue(dashboardMetrics.revenue, 'Pending'),
      detail: dashboardMetrics.mrr != null ? `${formatCurrencyValue(dashboardMetrics.mrr, '$0.00')} MRR` : null,
    },
    {
      id: 'cost',
      label: 'Runtime Cost',
      value: formatCurrencyValue(dashboardMetrics.cost, totalRuns > 0 ? '$0.00' : 'Pending'),
      detail: totalRuns > 0 ? `${formatCompactNumber(totalRuns, '0')} chats` : null,
    },
    {
      id: 'margin',
      label: 'Margin',
      value: formatMarginLabel(dashboardMetrics.revenue, dashboardMetrics.cost),
      detail: dashboardMetrics.marginRate != null ? formatPercentLabel(dashboardMetrics.marginRate) : null,
    },
    {
      id: 'users',
      label: 'Active Users',
      value: formatCompactNumber(dashboardMetrics.activeUsers, 'Pending'),
      detail: dashboardMetrics.totalUsers != null ? `${formatCompactNumber(dashboardMetrics.totalUsers, '0')} total` : null,
    },
    {
      id: 'chats',
      label: 'Chats',
      value: formatCompactNumber(dashboardMetrics.chats, '0'),
      detail: dashboardMetrics.llmCalls != null ? `${formatCompactNumber(dashboardMetrics.llmCalls, '0')} LLM calls` : null,
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-5">

        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          showBanner
          title="Overview"
          subtitle="Top-level performance, cost, access, usage, and build state."
          summaryItems={[]}
          actions={[{ id: 'refresh', label: 'Refresh', variant: 'outline' }]}
          onAction={(id) => id === 'refresh' && refresh()}
        />

        {nextStep && <NextStepCallout nextStep={nextStep} action={primaryAction} />}

        <KpiGrid items={kpiItems} />

        {isApprovalPending && (
          <Alert variant="warning">
            This build is waiting for approval before it can proceed. Open Build Studio to review the plan and respond.
          </Alert>
        )}
        {isApprovalRejected && (
          <Alert variant="destructive">
            A revision was requested on the last build. Continue the build session to address the feedback and resubmit.
          </Alert>
        )}

        {isDraft ? (
          <StudioInlineEmptyState
            title="No builds yet"
            description="Capture the first app brief to begin tracking this app through the build lifecycle. Artifact history, approval state, and runtime metrics will appear here once a build starts."
          />
        ) : (
          <div className="grid gap-5 xl:grid-cols-3">
            <BuildStatusPanel
              build={build}
              latestArtifact={latestArtifact}
              isApprovalPending={isApprovalPending}
              appId={appId}
            />
            <AppIntelligencePanel context={data.context} appId={appId} />
            <ActivityPanel
              snapshot={snapshot}
              latestRun={latestRun}
              totalRuns={totalRuns}
              appId={appId}
            />
          </div>
        )}

        {cfReport && <CarryForwardReportPanel report={cfReport} />}

      </div>
    </WorkspaceLayout>
  )
}
