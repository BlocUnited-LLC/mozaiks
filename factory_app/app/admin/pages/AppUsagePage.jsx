import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
} from '../../ui/components/StudioShared.jsx'
import { AppStudioHero, formatCompactNumber, formatCurrencyValue } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot, sumBy } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

const COL_HEADER = 'px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground'
const COL_CELL = 'px-4 py-3 text-sm text-muted-foreground tabular-nums'

function formatShortDate(iso) {
  if (!iso) return '-'
  try {
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
  } catch {
    return iso
  }
}

function normalizeUsageRun(run) {
  const inputTokens = Number(run?.prompt_tokens || 0)
  const outputTokens = Number(run?.completion_tokens || 0)
  const totalTokens = Number(run?.total_tokens || inputTokens + outputTokens)
  return {
    ...run,
    inputTokens,
    outputTokens,
    totalTokens,
    cost: Number(run?.estimated_cost_usd ?? run?.cost ?? 0),
    llmCalls: Number(run?.llm_calls || 0),
  }
}

function buildWorkflowGroups(usage, demoRuns = []) {
  const usageWorkflows = Array.isArray(usage?.by_workflow) ? usage.by_workflow : []
  const runRows = Array.isArray(usage?.by_run) && usage.by_run.length > 0
    ? usage.by_run.map(normalizeUsageRun)
    : (Array.isArray(demoRuns) ? demoRuns.map(normalizeUsageRun) : [])
  const groups = new Map()

  for (const row of usageWorkflows) {
    const key = row.workflow_name || 'Unknown workflow'
    groups.set(key, {
      workflow: key,
      sessions: [],
      inputTokens: Number(row.prompt_tokens || 0),
      outputTokens: Number(row.completion_tokens || 0),
      totalTokens: Number(row.total_tokens || 0),
      cost: Number(row.estimated_cost_usd || 0),
      count: Number(row.runs || 0),
      llmCalls: Number(row.llm_calls || 0),
    })
  }

  for (const run of runRows) {
    const key = run.workflow_name || 'Unknown workflow'
    if (!groups.has(key)) {
      groups.set(key, {
        workflow: key,
        sessions: [],
        inputTokens: 0,
        outputTokens: 0,
        totalTokens: 0,
        cost: 0,
        count: 0,
        llmCalls: 0,
      })
    }
    const group = groups.get(key)
    group.sessions.push(run)
    if (usageWorkflows.length === 0) {
      group.inputTokens += run.inputTokens
      group.outputTokens += run.outputTokens
      group.totalTokens += run.totalTokens
      group.cost += run.cost
      group.count += 1
      group.llmCalls += run.llmCalls || 1
    }
  }

  return Array.from(groups.values()).map((group) => {
    const count = group.count || group.sessions.length
    return {
      ...group,
      count,
      avgTokens: count > 0 ? group.totalTokens / count : 0,
      avgCost: count > 0 ? group.cost / count : 0,
    }
  }).sort((a, b) => b.totalTokens - a.totalTokens)
}

function exportBreakdownCsv(appId, groups) {
  const headers = ['workflow', 'runs', 'llm_calls', 'input_tokens', 'output_tokens', 'total_tokens', 'avg_tokens', 'estimated_cost', 'avg_cost']
  const lines = [
    headers.join(','),
    ...groups.map((g) => [
      JSON.stringify(g.workflow),
      g.count,
      g.llmCalls,
      g.inputTokens,
      g.outputTokens,
      g.totalTokens,
      Math.round(g.avgTokens),
      g.cost.toFixed(4),
      g.avgCost.toFixed(4),
    ].join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${appId}-usage.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function WorkflowGroupRow({ group, expanded, onToggle }) {
  return (
    <>
      <tr onClick={onToggle} className="border-b border-border cursor-pointer hover:bg-muted/25 transition-colors">
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] text-muted-foreground transition-transform duration-150 select-none ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <span className="font-semibold text-foreground text-sm">{group.workflow}</span>
            <span className="text-xs text-muted-foreground ml-1">{group.count} run{group.count !== 1 ? 's' : ''}</span>
          </div>
        </td>
        <td className={COL_CELL}>{formatCompactNumber(group.inputTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(group.outputTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(group.totalTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(Math.round(group.avgTokens), '0')}</td>
        <td className={COL_CELL}>{formatCurrencyValue(group.cost, '$0.00')}</td>
        <td className={COL_CELL}>{formatCurrencyValue(group.avgCost, '$0.0000')}</td>
        <td className={COL_CELL}>{formatCompactNumber(group.llmCalls, '0')}</td>
      </tr>
      {expanded && group.sessions.map((session) => (
        <tr key={session.chat_id} className="border-b border-border/50 bg-muted/15 hover:bg-muted/25 transition-colors">
          <td className="pl-10 pr-4 py-2.5">
            <div className="text-xs font-mono text-muted-foreground/80">{String(session.chat_id || '').slice(-10) || '-'}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {session.user_id || 'system'} · {formatShortDate(session.started_at || session.event_ts)}
            </div>
          </td>
          <td className={COL_CELL}>{formatCompactNumber(session.inputTokens, '0')}</td>
          <td className={COL_CELL}>{formatCompactNumber(session.outputTokens, '0')}</td>
          <td className={COL_CELL}>{formatCompactNumber(session.totalTokens, '0')}</td>
          <td className={COL_CELL}>-</td>
          <td className={COL_CELL}>{formatCurrencyValue(session.cost, '$0.00')}</td>
          <td className={COL_CELL}>-</td>
          <td className={COL_CELL}>{formatCompactNumber(session.llmCalls, '0')}</td>
        </tr>
      ))}
    </>
  )
}

export default function AppUsagePage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const [expandedWorkflows, setExpandedWorkflows] = useState(new Set())

  const toggleExpand = (workflowName) => {
    setExpandedWorkflows((prev) => {
      const next = new Set(prev)
      next.has(workflowName) ? next.delete(workflowName) : next.add(workflowName)
      return next
    })
  }

  if (loading) return <StudioLoadingState label="Loading app usage..." />
  if (error || !data) return <StudioErrorState title="Usage Unavailable" message={error || 'No usage data returned.'} />

  const snapshot = getAppStudioSnapshot(appId, data, dataMode)
  const usageTotals = snapshot.usage?.totals || {}
  const workflowGroups = buildWorkflowGroups(snapshot.usage, dataMode === 'demo' ? snapshot.runs : [])
  const totalInputTokens = Number(usageTotals.prompt_tokens || 0)
  const totalOutputTokens = Number(usageTotals.completion_tokens || 0)
  const totalTokens = Number(usageTotals.total_tokens || 0) || totalInputTokens + totalOutputTokens
  const totalCost = Number(usageTotals.estimated_cost_usd || 0)
  const totalRuns = Number(snapshot.stats.tracked_chats || 0)
  const totalLlmCalls = Number(usageTotals.llm_calls || 0)
  const costDrivers = workflowGroups.flatMap((group) => group.sessions)
    .sort((left, right) => Number(right.cost || 0) - Number(left.cost || 0))
    .slice(0, 3)
  const averageLatency = snapshot.runs.length > 0
    ? Math.round(sumBy(snapshot.runs, (run) => run.runtime_sec || 0) / snapshot.runs.length)
    : null

  const handleExportCsv = () => exportBreakdownCsv(appId, workflowGroups)
  const summaryItems = [
    { id: 'total', label: 'Tokens Used', value: formatCompactNumber(totalTokens, 'Pending'), detail: 'Input + output' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(totalCost), detail: 'Estimated or provider supplied' },
    { id: 'runs', label: 'Workflow Runs', value: formatCompactNumber(totalRuns, '0'), detail: 'Tracked executions' },
    { id: 'llmCalls', label: 'LLM Calls', value: formatCompactNumber(totalLlmCalls, '0'), detail: 'Metered calls' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Usage"
          currentSection="Usage"
          subtitle="Review measured runtime token usage and cost estimates."
          actions={[{ id: 'export-csv', label: 'Export CSV' }]}
          onAction={(actionId) => { if (actionId === 'export-csv') handleExportCsv() }}
          summaryItems={summaryItems}
        />

        <Panel title="Workflow breakdown" subtitle="Expand a workflow to see metered runs.">
          {workflowGroups.length > 0 ? (
            <div className="overflow-hidden rounded-2xl border border-border/42">
              <table className="min-w-full text-sm">
                <thead className="bg-background/34 border-b border-border/42">
                  <tr>
                    <th className={COL_HEADER}>Workflow</th>
                    <th className={COL_HEADER}>Input</th>
                    <th className={COL_HEADER}>Output</th>
                    <th className={COL_HEADER}>Total</th>
                    <th className={COL_HEADER}>Avg / run</th>
                    <th className={COL_HEADER}>Cost</th>
                    <th className={COL_HEADER}>Avg cost</th>
                    <th className={COL_HEADER}>LLM calls</th>
                  </tr>
                </thead>
                <tbody className="bg-card/24">
                  {workflowGroups.map((group) => (
                    <WorkflowGroupRow
                      key={group.workflow}
                      group={group}
                      expanded={expandedWorkflows.has(group.workflow)}
                      onToggle={() => toggleExpand(group.workflow)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <StudioInlineEmptyState
              title="No usage breakdown available yet"
              description="This view becomes informative once AG2 beta workflow agents produce metered LLM calls."
            />
          )}
        </Panel>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel title="Highest-cost activity" subtitle="Metered runs currently driving estimated spend.">
            {costDrivers.length > 0 ? (
              <div className="space-y-3">
                {costDrivers.map((run) => (
                  <div key={run.chat_id} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-semibold text-foreground">{run.workflow_name}</div>
                      <div className="text-sm text-muted-foreground">{formatCurrencyValue(run.cost, '$0.00')}</div>
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      {formatCompactNumber(run.totalTokens, '0')} tokens · {formatCompactNumber(run.llmCalls, '0')} LLM calls
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <StudioInlineEmptyState
                title="Cost drivers appear after runtime usage begins"
                description="Once the app is actively serving LLM traffic, this panel highlights the metered runs behind the largest cost footprint."
              />
            )}
          </Panel>

          <Panel title="Runtime posture" subtitle="Lifecycle and usage signals stay separated.">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                <div className="text-[12px] font-medium text-muted-foreground/82">Average latency</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{averageLatency != null ? `${averageLatency}s` : 'Pending'}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                <div className="text-[12px] font-medium text-muted-foreground/82">Tracked workflows</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.workflowNames.length, '0')}</div>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
