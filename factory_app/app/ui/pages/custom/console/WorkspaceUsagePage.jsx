import { useMemo, useState } from 'react'

import {
  CollectionToolbar,
  InlineEmptyState,
  PageHeader,
  ResourceList,
  SummaryStrip,
} from '@mozaiks/chat-ui/ui'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ConsoleErrorState,
  ConsoleLoadingState,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import { useWorkspaceConsoleData } from './useWorkspaceConsoleData.js'


function exportUsageCsv(rows) {
  const headers = ['workflow', 'apps', 'runs', 'input_tokens', 'output_tokens', 'total_tokens', 'cost', 'errors']
  const lines = [
    headers.join(','),
    ...rows.map((row) => [
      JSON.stringify(row.label),
      JSON.stringify(row.appsLabel),
      JSON.stringify(row.runs),
      JSON.stringify(row.inputTokens),
      JSON.stringify(row.outputTokens),
      JSON.stringify(row.totalTokens),
      JSON.stringify(row.cost),
      JSON.stringify(row.errors),
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

function buildWorkflowRows(runs) {
  const groups = new Map()

  for (const run of Array.isArray(runs) ? runs : []) {
    const key = run?.workflow_name || 'Unknown workflow'
    if (!groups.has(key)) {
      groups.set(key, {
        label: key,
        apps: new Set(),
        runs: 0,
        inputTokens: 0,
        outputTokens: 0,
        cost: 0,
        errors: 0,
      })
    }

    const current = groups.get(key)
    current.runs += 1
    current.inputTokens += Number(run?.prompt_tokens || 0)
    current.outputTokens += Number(run?.completion_tokens || 0)
    current.cost += Number(run?.cost || 0)
    current.errors += Number(run?.errors || 0)
    if (run?.app_name || run?.app_id) current.apps.add(run.app_name || run.app_id)
  }

  return Array.from(groups.values())
    .map((row) => {
      const totalTokens = row.inputTokens + row.outputTokens
      const appNames = Array.from(row.apps)
      return {
        ...row,
        id: row.label,
        totalTokens,
        appsLabel: appNames.length > 1 ? `${appNames.length} apps` : appNames[0] || 'Workspace',
        searchText: `${row.label} ${appNames.join(' ')}`.toLowerCase(),
      }
    })
    .sort((left, right) => right.totalTokens - left.totalTokens || right.cost - left.cost)
}

export default function WorkspaceUsagePage() {
  const { workspaceStats, workspaceRuns, loading, error, dataMode } = useWorkspaceConsoleData('Workspace usage could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const workflowRows = useMemo(() => buildWorkflowRows(workspaceRuns), [workspaceRuns])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return workflowRows
    return workflowRows.filter((row) => row.searchText.includes(search))
  }, [workflowRows, searchValue])
  const totalInputTokens = Number(workspaceStats.total_prompt_tokens || 0)
  const totalOutputTokens = Number(workspaceStats.total_completion_tokens || 0)
  const totalTokens = totalInputTokens + totalOutputTokens
  const totalCost = Number(workspaceStats.total_cost || 0)
  const totalRuns = Number(workspaceStats.tracked_chats || workspaceRuns.length || 0)
  const summaryItems = [
    { id: 'tokens', label: 'Tokens Used', value: formatCompactNumber(totalTokens, 'Pending'), detail: 'Input + output' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(totalCost, 'Pending'), detail: 'Observed spend' },
    { id: 'runs', label: 'Workflow Runs', value: formatCompactNumber(totalRuns, '0'), detail: 'Tracked executions' },
    { id: 'avgCost', label: 'Avg Cost / Run', value: totalRuns > 0 ? formatCurrencyValue(totalCost / totalRuns, 'Pending') : 'Pending' },
  ]
  const columns = [
    {
      id: 'workflow',
      header: 'Workflow',
      width: '24%',
      render: (row) => (
        <div>
          <div className="font-semibold text-foreground">{row.label}</div>
          <div className="mt-1 text-sm text-muted-foreground/88">{row.appsLabel}</div>
        </div>
      ),
    },
    {
      id: 'runs',
      header: 'Runs',
      width: '10%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.runs, '0'),
    },
    {
      id: 'input',
      header: 'Input',
      width: '12%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.inputTokens, '0'),
    },
    {
      id: 'output',
      header: 'Output',
      width: '12%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.outputTokens, '0'),
    },
    {
      id: 'total',
      header: 'Total',
      width: '12%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.totalTokens, '0'),
    },
    {
      id: 'average',
      header: 'Avg / Run',
      width: '12%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.runs > 0 ? row.totalTokens / row.runs : 0, '0'),
    },
    {
      id: 'cost',
      header: 'Cost',
      width: '10%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCurrencyValue(row.cost, '$0.00'),
    },
    {
      id: 'errors',
      header: 'Errors',
      width: '8%',
      render: (row) => <StatusPill tone={row.errors > 0 ? 'warning' : 'success'}>{formatCompactNumber(row.errors, '0')}</StatusPill>,
    },
  ]

  if (loading) return <ConsoleLoadingState label="Loading workspace usage…" />
  if (error) return <ConsoleErrorState title="Workspace Usage Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Usage"
          subtitle="Track workspace metering, model spend, and workflow activity."
          actions={[
            { id: 'export', label: 'Export CSV', variant: 'outline' },
          ]}
          onAction={() => exportUsageCsv(visibleRows)}
        />

        <SummaryStrip items={summaryItems} />

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
    </AdminWorkspaceLayout>
  )
}
