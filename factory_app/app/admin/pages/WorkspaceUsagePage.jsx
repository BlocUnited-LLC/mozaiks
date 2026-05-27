import { useMemo, useState } from 'react'

import {
  CollectionToolbar,
  InlineEmptyState,
  ResourceList,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioLoadingState,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber, formatCurrencyValue } from './AppStudioChrome.jsx'
import { useWorkspaceStudioData } from './useWorkspaceStudioData.js'


function buildAppRows(runs, appNameMap) {
  const groups = new Map()

  for (const run of Array.isArray(runs) ? runs : []) {
    const appId = run?.app_id || 'unknown'
    const appName = appNameMap[appId] || appId || 'Unknown app'
    if (!groups.has(appId)) {
      groups.set(appId, {
        id: appId,
        label: appName,
        runs: 0,
        inputTokens: 0,
        outputTokens: 0,
        cost: 0,
        errors: 0,
        workflows: new Set(),
      })
    }

    const current = groups.get(appId)
    current.runs += 1
    current.inputTokens += Number(run?.prompt_tokens || 0)
    current.outputTokens += Number(run?.completion_tokens || 0)
    current.cost += Number(run?.cost || 0)
    current.errors += Number(run?.errors || 0)
    if (run?.workflow_name) current.workflows.add(run.workflow_name)
  }

  return Array.from(groups.values())
    .map((row) => {
      const totalTokens = row.inputTokens + row.outputTokens
      const workflows = Array.from(row.workflows)
      return {
        ...row,
        totalTokens,
        avgTokens: row.runs > 0 ? totalTokens / row.runs : 0,
        avgCost: row.runs > 0 ? row.cost / row.runs : 0,
        workflowCount: workflows.length,
        workflowLabel: workflows.length === 1 ? workflows[0] : `${workflows.length} workflows`,
        searchText: `${row.label} ${workflows.join(' ')}`.toLowerCase(),
      }
    })
    .sort((left, right) => right.totalTokens - left.totalTokens || right.cost - left.cost)
}

export default function WorkspaceUsagePage() {
  const { apps, workspaceStats, workspaceRuns, loading, error, dataMode } = useWorkspaceStudioData('Workspace usage could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const appNameMap = useMemo(() => {
    const map = {}
    for (const app of Array.isArray(apps) ? apps : []) {
      const id = app.app_id || app.app?.app_id || app.id
      const name = app.name || app.app?.name || id
      if (id) map[id] = name
    }
    return map
  }, [apps])

  const appRows = useMemo(() => buildAppRows(workspaceRuns, appNameMap), [workspaceRuns, appNameMap])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return appRows
    return appRows.filter((row) => row.searchText.includes(search))
  }, [appRows, searchValue])
  const totalInputTokens = Number(workspaceStats.total_prompt_tokens || 0)
  const totalOutputTokens = Number(workspaceStats.total_completion_tokens || 0)
  const totalTokens = totalInputTokens + totalOutputTokens
  const totalCost = Number(workspaceStats.total_cost || 0)
  const totalRuns = Number(workspaceStats.tracked_chats || workspaceRuns.length || 0)
  const handleExportCsv = () => {
    const headers = [
      'app',
      'runs',
      'input_tokens',
      'output_tokens',
      'total_tokens',
      'avg_tokens',
      'cost',
      'avg_cost',
      'errors',
      'workflows',
    ]
    const lines = [
      headers.join(','),
      ...visibleRows.map((row) => [
        JSON.stringify(row.label),
        row.runs,
        row.inputTokens,
        row.outputTokens,
        row.totalTokens,
        Math.round(row.avgTokens),
        Number(row.cost).toFixed(4),
        Number(row.avgCost).toFixed(4),
        row.errors,
        JSON.stringify(row.workflowLabel),
      ].join(',')),
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'workspace-usage.csv'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }
  const handleHeaderAction = (actionId) => {
    if (actionId === 'export-csv') {
      handleExportCsv()
    }
  }
  const summaryItems = [
    { id: 'tokens', label: 'Tokens Used', value: formatCompactNumber(totalTokens, 'Pending'), detail: 'Input + output' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(totalCost, 'Pending'), detail: 'Observed spend' },
    { id: 'runs', label: 'Workflow Runs', value: formatCompactNumber(totalRuns, '0'), detail: 'Tracked executions' },
    { id: 'avgCost', label: 'Avg Cost / Run', value: totalRuns > 0 ? formatCurrencyValue(totalCost / totalRuns, 'Pending') : 'Pending' },
  ]
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '22%',
      render: (row) => (
        <div>
          <div className="font-semibold text-foreground">{row.label}</div>
          <div className="mt-1 text-xs text-muted-foreground">{row.workflowLabel}</div>
        </div>
      ),
    },
    {
      id: 'runs',
      header: 'Runs',
      width: '8%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.runs, '0'),
    },
    {
      id: 'input',
      header: 'Input tok.',
      width: '11%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.inputTokens, '0'),
    },
    {
      id: 'output',
      header: 'Output tok.',
      width: '11%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.outputTokens, '0'),
    },
    {
      id: 'total',
      header: 'Total tok.',
      width: '11%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.totalTokens, '0'),
    },
    {
      id: 'avg_tokens',
      header: 'Avg tok./run',
      width: '11%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(Math.round(row.avgTokens), '0'),
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
      header: 'Avg cost/run',
      width: '10%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCurrencyValue(row.avgCost, '$0.00'),
    },
    {
      id: 'errors',
      header: 'Errors',
      width: '6%',
      render: (row) => <StatusPill tone={row.errors > 0 ? 'warning' : 'success'}>{formatCompactNumber(row.errors, '0')}</StatusPill>,
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
          actions={[{ id: 'export-csv', label: 'Export CSV' }]}
          onAction={handleHeaderAction}
          summaryItems={summaryItems}
        />

        <section className="space-y-4">
          <div className="space-y-4">
            <CollectionToolbar
              searchValue={searchValue}
              onSearchChange={setSearchValue}
              searchPlaceholder="Search workflows or apps..."
              actions={dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
            />

            {visibleRows.length > 0 ? (
              <ResourceList items={visibleRows} columns={columns} getItemId={(row) => row.id} />
            ) : (
              <InlineEmptyState
                title="No workflow usage yet"
                description="Usage appears after workflow runs produce token, cost, or error data."
              />
            )}
          </div>
        </section>
      </div>
    </WorkspaceLayout>
  )
}
