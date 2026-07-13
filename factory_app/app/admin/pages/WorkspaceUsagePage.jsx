import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  Alert,
  CollectionToolbar,
  InlineEmptyState,
  ResourceList,
  UsageTrendPanel,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  SegmentedControl,
  StudioErrorState,
  StudioLoadingState,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber, formatCurrencyValue } from './AppStudioChrome.jsx'
import { getAppDisplayName } from './appStudioModel.js'
import PricingHealthPanel from './PricingHealthPanel.jsx'
import {
  USAGE_TREND_METRICS,
  buildUsageTrendSeries,
  getUsageRows,
} from './usagePresentation.js'
import { useWorkspaceStudioData } from './useWorkspaceStudioData.js'

const PAGE_SIZE = 10

const DashboardIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
  </svg>
)

function buildAppRows(usage, appNameMap, demoRuns = []) {
  const groups = new Map()
  const sourceRows = Array.isArray(usage?.by_run) && usage.by_run.length > 0
    ? usage.by_run
    : Array.isArray(usage?.events) && usage.events.length > 0
      ? usage.events
    : (Array.isArray(demoRuns) ? demoRuns : [])

  for (const run of sourceRows) {
    const appId = run?.app_id || null
    if (!appId) continue
    const appName = run?.app_name || appNameMap[appId] || appId
    if (!groups.has(appId)) {
      groups.set(appId, {
        id: appId,
        label: appName,
        runs: 0,
        inputTokens: 0,
        outputTokens: 0,
        cost: 0,
        llmCalls: 0,
        runIds: new Set(),
        workflows: new Set(),
      })
    }
    const current = groups.get(appId)
    const runId = run?.chat_id || run?.run_id || run?.id
    if (runId) {
      current.runIds.add(String(runId))
    } else {
      current.runs += 1
    }
    current.llmCalls += Number(run?.llm_calls || 1)
    current.inputTokens += Number(run?.prompt_tokens || 0)
    current.outputTokens += Number(run?.completion_tokens || 0)
    current.cost += Number(run?.estimated_cost_usd ?? run?.cost ?? 0)
    if (run?.workflow_name) current.workflows.add(run.workflow_name)
  }

  // Include all registered workspace apps even if they have no usage yet
  for (const [appId, appName] of Object.entries(appNameMap)) {
    if (!groups.has(appId)) {
      groups.set(appId, {
        id: appId,
        label: appName,
        runs: 0,
        inputTokens: 0,
        outputTokens: 0,
        cost: 0,
        llmCalls: 0,
        runIds: new Set(),
        workflows: new Set(),
      })
    }
  }

  return Array.from(groups.values())
    .map((row) => {
      const totalTokens = row.inputTokens + row.outputTokens
      const workflows = Array.from(row.workflows)
      const runCount = row.runIds.size || row.runs
      return {
        ...row,
        runs: runCount,
        runIds: undefined,
        totalTokens,
        avgCost: runCount > 0 ? row.cost / runCount : 0,
        workflowCount: workflows.length,
        workflowLabel: workflows.length === 1 ? workflows[0] : `${workflows.length} workflows`,
        searchText: `${row.label} ${workflows.join(' ')}`.toLowerCase(),
      }
    })
    .sort((left, right) => right.totalTokens - left.totalTokens || right.cost - left.cost)
}

function PageControls({ page, totalPages, total, onPageChange }) {
  if (totalPages <= 1) return null
  const from = (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)
  return (
    <div className="flex items-center justify-between px-1 pt-1 text-xs text-muted-foreground">
      <span>{from}–{to} of {total}</span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
          className="px-2 py-1 rounded hover:bg-muted/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          ←
        </button>
        <span className="px-1">{page} / {totalPages}</span>
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page === totalPages}
          className="px-2 py-1 rounded hover:bg-muted/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          →
        </button>
      </div>
    </div>
  )
}

const columns = [
  {
    id: 'app',
    header: 'App',
    width: '30%',
    render: (row) => (
      <div>
        <div className="font-semibold text-foreground">{row.label}</div>
        <div className="mt-1 text-xs text-muted-foreground">{row.workflowLabel}</div>
      </div>
    ),
  },
  {
    id: 'runs',
    header: 'Chats',
    width: '8%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCompactNumber(row.runs, '0'),
  },
  {
    id: 'input',
    header: 'Input tok.',
    width: '12%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCompactNumber(row.inputTokens, '0'),
  },
  {
    id: 'output',
    header: 'Output tok.',
    width: '12%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCompactNumber(row.outputTokens, '0'),
  },
  {
    id: 'total',
    header: 'Total tok.',
    width: '12%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCompactNumber(row.totalTokens, '0'),
  },
  {
    id: 'cost',
    header: 'Cost',
    width: '10%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCurrencyValue(row.cost, '$0.00'),
  },
  {
    id: 'avg_cost',
    header: 'Avg cost/chat',
    width: '12%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCurrencyValue(row.avgCost, '$0.00'),
  },
  {
    id: 'action',
    header: '',
    width: '10%',
    headerClassName: 'text-right',
    cellClassName: 'text-right',
    render: (row) => (
      <ActionButton
        onClick={(event) => {
          event.stopPropagation()
          row.onDashboard?.(row)
        }}
        size="sm"
        variant="ghost"
        className="text-muted-foreground hover:text-foreground px-2"
        aria-label="Dashboard"
      >
        <span className="inline-flex items-center gap-1.5">
          <DashboardIcon />
          <span>Dashboard</span>
        </span>
      </ActionButton>
    ),
  },
]

function UsageMobileItem({ row, onDashboard }) {
  return (
    <article
      className="rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5 cursor-pointer hover:bg-card/50 transition-colors"
      onClick={() => onDashboard(row)}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-semibold text-foreground">{row.label}</div>
          <div className="mt-1 text-xs text-muted-foreground">{row.workflowLabel}</div>
        </div>
        <ActionButton
          onClick={(event) => {
            event.stopPropagation()
            onDashboard(row)
          }}
          size="sm"
          variant="ghost"
          className="text-muted-foreground hover:text-foreground px-2"
          aria-label="Dashboard"
        >
          <DashboardIcon />
        </ActionButton>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-[12px] text-muted-foreground">Chats</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">{formatCompactNumber(row.runs, '0')}</div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">Cost</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">{formatCurrencyValue(row.cost, '$0.00')}</div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">Tokens</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">{formatCompactNumber(row.totalTokens, '0')}</div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">Avg/chat</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">{formatCurrencyValue(row.avgCost, '$0.00')}</div>
        </div>
      </div>
    </article>
  )
}

export default function WorkspaceUsagePage() {
  const navigate = useNavigate()
  const { apps, workspaceRuns, workspaceUsage, loading, error, dataMode } = useWorkspaceStudioData('Workspace usage could not be loaded.')
  const [searchValue, setSearchValue] = useState('')
  const [page, setPage] = useState(1)
  const [chartMetric, setChartMetric] = useState('cost')

  const appNameMap = useMemo(() => {
    const map = {}
    for (const app of Array.isArray(apps) ? apps : []) {
      const id = app.app_id || app.app?.app_id || app.id
      const name = getAppDisplayName(app.app || app) || id
      if (id) map[id] = name
    }
    return map
  }, [apps])

  const appRows = useMemo(
    () => buildAppRows(workspaceUsage, appNameMap, dataMode === 'demo' ? workspaceRuns : []),
    [workspaceUsage, workspaceRuns, appNameMap, dataMode],
  )
  const openDashboard = (row) => {
    if (!row?.id) return
    navigate(`/apps/${encodeURIComponent(row.id)}/usage`)
  }
  const rowsWithActions = useMemo(
    () => appRows.map((row) => ({ ...row, onDashboard: openDashboard })),
    [appRows],
  )
  const filteredRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    return search ? rowsWithActions.filter((row) => row.searchText.includes(search)) : rowsWithActions
  }, [rowsWithActions, searchValue])

  useEffect(() => { setPage(1) }, [searchValue])

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE))
  const visibleRows = filteredRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const totalInputTokens = appRows.reduce((total, row) => total + Number(row.inputTokens || 0), 0)
  const totalOutputTokens = appRows.reduce((total, row) => total + Number(row.outputTokens || 0), 0)
  const totalTokens = totalInputTokens + totalOutputTokens
  const totalCost = appRows.reduce((total, row) => total + Number(row.cost || 0), 0)
  const totalRuns = appRows.reduce((total, row) => total + Number(row.runs || 0), 0)
  const usageRows = useMemo(
    () => getUsageRows(workspaceUsage, dataMode === 'demo' ? workspaceRuns : []),
    [workspaceUsage, workspaceRuns, dataMode],
  )
  const trendData = useMemo(
    () => buildUsageTrendSeries(usageRows, chartMetric).map((point) => ({
      ...point,
      extras: [
        { label: 'Date', value: point.detail || point.label },
        { label: 'Cost', value: formatCurrencyValue(point.cost, '$0.0000') },
        { label: 'Tokens', value: formatCompactNumber(point.tokens, '0') },
        { label: 'Chats', value: String(point.runs) },
      ],
    })),
    [usageRows, chartMetric],
  )
  const activeAppCount = appRows.filter((row) => row.totalTokens > 0 || row.cost > 0 || row.runs > 0).length
  const topApp = appRows.find((row) => row.totalTokens > 0 || row.cost > 0 || row.runs > 0)
  const costSource = workspaceUsage?.cost_source
  const pricingHealth = workspaceUsage?.pricing_health
  const costDetail = pricingHealth?.status === 'ready'
    ? 'Catalog priced'
    : pricingHealth?.status === 'unpriced_models'
      ? 'Unpriced models'
      : costSource === 'default_table'
        ? 'Fallback list prices'
        : costSource === 'override'
          ? 'Override rates'
          : 'Estimated or provider supplied'

  const chartConfig = {
    cost: {
      label: 'Total spend',
      value: formatCurrencyValue(totalCost, 'Pending'),
      detail: costDetail,
      formatPointValue: (value) => formatCurrencyValue(value, '$0.00'),
    },
    tokens: {
      label: 'Total tokens',
      value: formatCompactNumber(totalTokens, 'Pending'),
      detail: 'Input + output',
      formatPointValue: (value) => formatCompactNumber(value, '0'),
    },
    runs: {
      label: 'Chats',
      value: formatCompactNumber(totalRuns, '0'),
      detail: 'Tracked chats',
      formatPointValue: (value) => formatCompactNumber(value, '0'),
    },
  }[chartMetric]
  const sideItems = [
    { id: 'input', label: 'Input tokens', value: formatCompactNumber(totalInputTokens, '0') },
    { id: 'output', label: 'Output tokens', value: formatCompactNumber(totalOutputTokens, '0') },
    { id: 'apps', label: 'Apps with usage', value: formatCompactNumber(activeAppCount, '0'), detail: `${formatCompactNumber(appRows.length, '0')} registered apps` },
    {
      id: 'top-app',
      label: 'Top app',
      value: topApp?.label || 'No usage yet',
      detail: topApp ? `${formatCurrencyValue(topApp.cost, '$0.00')} across ${formatCompactNumber(topApp.runs, '0')} chats` : 'Start a chat to populate this view.',
    },
  ]

  if (loading) return <StudioLoadingState label="Loading workspace usage…" />
  if (error) return <StudioErrorState title="Workspace Usage Unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Usage"
          subtitle="Track workspace metering, model spend, and workflow activity."
          actions={null}
          onAction={null}
        />

        <UsageTrendPanel
          title="Workspace usage"
          subtitle="Spend, token volume, and chats across your apps."
          metricLabel={chartConfig.label}
          metricValue={chartConfig.value}
          metricDetail={chartConfig.detail}
          data={trendData}
          sideItems={sideItems}
          formatPointValue={chartConfig.formatPointValue}
          action={(
            <SegmentedControl
              options={USAGE_TREND_METRICS}
              value={chartMetric}
              onChange={setChartMetric}
              className="border-b-0"
            />
          )}
        />

        {costSource === 'not_configured' && totalCost === 0 && (
          <Alert
            variant="warning"
            message="Cost estimates show $0.00 because one or more models are not priced. Refresh the provider catalog or set a pricing override."
          />
        )}

        {costSource === 'default_table' && (
          <Alert
            variant="info"
            message="Some costs are estimated from the local fallback table. Refresh the generated provider catalog or add override rates before relying on these numbers."
          />
        )}

        <PricingHealthPanel usage={workspaceUsage} />

        <section className="space-y-4">
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={setSearchValue}
            searchPlaceholder="Search apps or workflows..."
            actions={dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          />

          {visibleRows.length > 0 ? (
            <div className="space-y-2">
              <ResourceList
                items={visibleRows}
                columns={columns}
                getItemId={(row) => row.id}
                onRowClick={openDashboard}
                renderMobileItem={(row) => <UsageMobileItem row={row} onDashboard={openDashboard} />}
              />
              <PageControls
                page={page}
                totalPages={totalPages}
                total={filteredRows.length}
                onPageChange={setPage}
              />
            </div>
          ) : (
            <InlineEmptyState
              title="No workflow usage yet"
              description="Usage appears after chats produce token, cost, or error data."
            />
          )}
        </section>
      </div>
    </WorkspaceLayout>
  )
}
