import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { AppStudioHero, formatCompactNumber, formatCurrencyValue } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot, sumBy } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

function formatShortDate(iso) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
  } catch { return iso }
}

function buildWorkflowGroups(runs) {
  const groups = new Map()
  for (const run of Array.isArray(runs) ? runs : []) {
    const key = run.workflow_name || 'Unknown workflow'
    if (!groups.has(key)) groups.set(key, { workflow: key, sessions: [] })
    groups.get(key).sessions.push(run)
  }
  return Array.from(groups.values()).map((g) => {
    const inputTokens = g.sessions.reduce((s, r) => s + Number(r.prompt_tokens || 0), 0)
    const outputTokens = g.sessions.reduce((s, r) => s + Number(r.completion_tokens || 0), 0)
    const cost = g.sessions.reduce((s, r) => s + Number(r.cost || 0), 0)
    const errors = g.sessions.reduce((s, r) => s + Number(r.errors || 0), 0)
    const count = g.sessions.length
    const totalTokens = inputTokens + outputTokens
    return {
      ...g,
      inputTokens,
      outputTokens,
      totalTokens,
      cost,
      errors,
      count,
      avgTokens: count > 0 ? totalTokens / count : 0,
      avgCost: count > 0 ? cost / count : 0,
    }
  }).sort((a, b) => b.totalTokens - a.totalTokens)
}

function exportBreakdownCsv(appId, groups) {
  const headers = ['workflow', 'sessions', 'input_tokens', 'output_tokens', 'total_tokens', 'avg_tokens', 'cost', 'avg_cost', 'errors']
  const lines = [
    headers.join(','),
    ...groups.map((g) => [
      JSON.stringify(g.workflow), g.count, g.inputTokens, g.outputTokens,
      g.totalTokens, Math.round(g.avgTokens), g.cost.toFixed(4), g.avgCost.toFixed(4), g.errors,
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

const COL_HEADER = 'px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground'
const COL_CELL = 'px-4 py-3 text-sm text-muted-foreground tabular-nums'

function WorkflowGroupRow({ group, expanded, onToggle }) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-border cursor-pointer hover:bg-muted/25 transition-colors"
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] text-muted-foreground transition-transform duration-150 select-none ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <span className="font-semibold text-foreground text-sm">{group.workflow}</span>
            <span className="text-xs text-muted-foreground ml-1">{group.count} session{group.count !== 1 ? 's' : ''}</span>
          </div>
        </td>
        <td className={COL_CELL}>{formatCompactNumber(group.inputTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(group.outputTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(group.totalTokens, '0')}</td>
        <td className={COL_CELL}>{formatCompactNumber(Math.round(group.avgTokens), '0')}</td>
        <td className={COL_CELL}>{formatCurrencyValue(group.cost, '$0.00')}</td>
        <td className={COL_CELL}>{formatCurrencyValue(group.avgCost, '$0.0000')}</td>
        <td className="px-4 py-3">
          <StatusPill tone={group.errors > 0 ? 'warning' : 'success'}>{formatCompactNumber(group.errors, '0')}</StatusPill>
        </td>
      </tr>

      {expanded && group.sessions.map((session) => (
        <tr key={session.chat_id} className="border-b border-border/50 bg-muted/15 hover:bg-muted/25 transition-colors">
          <td className="pl-10 pr-4 py-2.5">
            <div className="text-xs font-mono text-muted-foreground/80">{String(session.chat_id || '').slice(-10) || '—'}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {session.user_id || 'system'} · {formatShortDate(session.started_at)}
              {session.runtime_sec ? ` · ${Math.round(session.runtime_sec)}s` : ''}
            </div>
          </td>
          <td className={COL_CELL}>{formatCompactNumber(session.prompt_tokens, '0')}</td>
          <td className={COL_CELL}>{formatCompactNumber(session.completion_tokens, '0')}</td>
          <td className={COL_CELL}>{formatCompactNumber((session.prompt_tokens || 0) + (session.completion_tokens || 0), '0')}</td>
          <td className={COL_CELL}>—</td>
          <td className={COL_CELL}>{formatCurrencyValue(session.cost, '$0.00')}</td>
          <td className={COL_CELL}>—</td>
          <td className="px-4 py-2.5">
            {Number(session.errors || 0) > 0
              ? <StatusPill tone="warning">{session.errors}</StatusPill>
              : <span className="text-xs text-muted-foreground">—</span>
            }
          </td>
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

  if (loading) return <StudioLoadingState label="Loading app usage…" />
  if (error || !data) return <StudioErrorState title="Usage Unavailable" message={error || 'No usage data returned.'} />

  const snapshot = getAppStudioSnapshot(appId, data, dataMode)
  const totalInputTokens = Number(snapshot.stats.total_prompt_tokens || 0)
  const totalOutputTokens = Number(snapshot.stats.total_completion_tokens || 0)
  const totalTokens = Number(snapshot.usageRecord?.tokens_used || 0) || totalInputTokens + totalOutputTokens
  const totalRuns = snapshot.runs.length || snapshot.stats.tracked_chats || 0
  const workflowGroups = buildWorkflowGroups(snapshot.runs)
  const handleExportCsv = () => {
    exportBreakdownCsv(appId, workflowGroups)
  }
  const handleHeaderAction = (actionId) => {
    if (actionId === 'export-csv') {
      handleExportCsv()
    }
  }
  const costDrivers = snapshot.runs
    .slice()
    .sort((left, right) => Number(right.cost || 0) - Number(left.cost || 0))
    .slice(0, 3)
  const errorRows = snapshot.runs.filter((run) => Number(run.errors || 0) > 0).slice(0, 3)
  const averageLatency =
    snapshot.runs.length > 0
      ? Math.round(sumBy(snapshot.runs, (run) => run.runtime_sec || 0) / snapshot.runs.length)
      : null
  const summaryItems = [
    { id: 'total', label: 'Tokens Used', value: formatCompactNumber(totalTokens, 'Pending'), detail: 'Input + output' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(snapshot.usageRecord?.llm_cost_usd ?? snapshot.stats.total_cost), detail: 'Observed spend' },
    { id: 'runs', label: 'Workflow Runs', value: formatCompactNumber(totalRuns, '0'), detail: 'Tracked executions' },
    { id: 'avgCost', label: 'Avg Cost / Run', value: totalRuns > 0 ? formatCurrencyValue((snapshot.usageRecord?.llm_cost_usd ?? snapshot.stats.total_cost) / totalRuns, 'Pending') : 'Pending' },
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
          subtitle="Review tokens, cost, workflow runs, and usage drivers."
          actions={[{ id: 'export-csv', label: 'Export CSV' }]}
          onAction={handleHeaderAction}
          summaryItems={summaryItems}
        />

        <Panel
          title="Workflow breakdown"
          subtitle="Expand a workflow to see individual sessions."
        >
          {workflowGroups.length > 0 ? (
            <div className="overflow-hidden rounded-2xl border border-border/42">
              <table className="min-w-full text-sm">
                <thead className="bg-background/34 border-b border-border/42">
                  <tr>
                    <th className={COL_HEADER}>Workflow</th>
                    <th className={COL_HEADER}>Input</th>
                    <th className={COL_HEADER}>Output</th>
                    <th className={COL_HEADER}>Total</th>
                    <th className={COL_HEADER}>Avg / session</th>
                    <th className={COL_HEADER}>Cost</th>
                    <th className={COL_HEADER}>Avg cost</th>
                    <th className={COL_HEADER}>Errors</th>
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
              description="This view becomes informative once the app has tracked runtime traffic across one or more workflows."
            />
          )}
        </Panel>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel title="Highest-cost activity" subtitle="Workflows currently driving spend.">
            {costDrivers.length > 0 ? (
              <div className="space-y-3">
                {costDrivers.map((run) => (
                  <div key={run.chat_id} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-semibold text-foreground">{run.workflow_name}</div>
                      <div className="text-sm text-muted-foreground">{formatCurrencyValue(run.cost, '$0.00')}</div>
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">
                      {formatCompactNumber((run.prompt_tokens || 0) + (run.completion_tokens || 0), '0')} tokens · {formatCompactNumber(run.tool_calls, '0')} tool calls
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <StudioInlineEmptyState
                title="Cost drivers appear after runtime usage begins"
                description="Once the app is actively serving requests, this panel will highlight the workflows behind the largest cost footprint."
              />
            )}
          </Panel>

          <div className="space-y-6">
            <Panel title="Runtime errors" subtitle="Recent workflows with errors.">
              {errorRows.length > 0 ? (
                <div className="space-y-3">
                  {errorRows.map((run) => (
                    <div key={run.chat_id} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-semibold text-foreground">{run.workflow_name}</div>
                        <div className="text-sm text-muted-foreground">{formatCompactNumber(run.errors, '0')} errors</div>
                      </div>
                      <div className="mt-2 text-sm text-muted-foreground">Started by {run.user_id || 'operator'} · {run.runtime_sec ? `${Math.round(run.runtime_sec)}s runtime` : 'runtime pending'}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <StudioInlineEmptyState
                  title="No error trend yet"
                  description="Once usage accumulates, this panel will show where workflow failures are concentrating."
                />
              )}
            </Panel>

            <Panel title="Latency" subtitle="High-level response-time posture.">
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
      </div>
    </WorkspaceLayout>
  )
}
