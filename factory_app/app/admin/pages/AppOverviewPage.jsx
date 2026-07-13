import { useMemo } from 'react'
import { useParams } from 'react-router-dom'

import { Alert } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton,
  Metric,
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
  const hasRuns = totalRuns > 0
  const latestWorkflow = latestRun?.workflow_name || null

  return (
    <Panel title="Activity" subtitle="Runtime usage and recent runs.">
      <div className="grid grid-cols-2 gap-3">
        <Metric
          label="Chats"
          value={formatCompactNumber(totalRuns, '0')}
          detail={latestRun ? formatRelativeTime(latestRun.started_at) : 'No runs yet'}
        />
        <Metric
          label="Latest workflow"
          value={latestWorkflow || '—'}
          detail={latestRun ? formatRelativeTime(latestRun.started_at) : 'No runs yet'}
        />
      </div>

      <div className="my-4 border-t border-border/30" />

      {latestRun ? (
        <>
          <Metric
            label="Latest run"
            value={latestRun.workflow_name || 'Workflow run'}
            detail={`${latestRun.user_id || 'Operator'} · ${formatRelativeTime(latestRun.started_at)}`}
          />
          {latestRun.status != null && (
            <div className="mt-2">
              <StatusPill tone={runStatusTone(latestRun.status)}>
                {runStatusLabel(latestRun.status)}
              </StatusPill>
            </div>
          )}
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

  const summaryItems = [
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
      <div className="space-y-6">

        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          showBanner
          nextStep={nextStep}
          nextStepAction={primaryAction}
          title="Overview"
          subtitle="Top-level app performance, cost, access, usage, and build state."
          currentSection="overview"
          summaryItems={summaryItems}
          actions={[{ id: 'refresh', label: 'Refresh', variant: 'outline' }]}
          onAction={(id) => id === 'refresh' && refresh()}
        />

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
          <div className="grid gap-6 xl:grid-cols-2">
            <BuildStatusPanel
              build={build}
              latestArtifact={latestArtifact}
              isApprovalPending={isApprovalPending}
              appId={appId}
            />
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
