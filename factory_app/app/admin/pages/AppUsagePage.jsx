import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  SegmentedControl,
} from '../../ui/components/ConsoleShared.jsx'
import { AppConsoleHero, formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import { getAppConsoleSnapshot, groupBy, sumBy } from './appConsoleDataHelpers.js'
import { useAppConsoleData } from './useAppConsoleData.js'

const BREAKDOWN_OPTIONS = [
  { value: 'workflow', label: 'By Workflow' },
  { value: 'user', label: 'By User' },
  { value: 'model', label: 'By Model' },
  { value: 'tool', label: 'By Tool' },
  { value: 'agent', label: 'By Agent' },
]

function buildBreakdownRows(snapshot, mode) {
  if (mode === 'workflow') {
    return groupBy(
      snapshot.runs,
      (run) => run.workflow_name,
      (run) => ({
        label: run.workflow_name,
        runs: 1,
        inputTokens: Number(run.prompt_tokens || 0),
        outputTokens: Number(run.completion_tokens || 0),
        cost: Number(run.cost || 0),
        errors: Number(run.errors || 0),
        detail: 'Workflow activity',
      }),
      (current, run) => ({
        ...current,
        runs: current.runs + 1,
        inputTokens: current.inputTokens + Number(run.prompt_tokens || 0),
        outputTokens: current.outputTokens + Number(run.completion_tokens || 0),
        cost: current.cost + Number(run.cost || 0),
        errors: current.errors + Number(run.errors || 0),
      }),
    )
      .map((row) => ({
        ...row,
        totalTokens: row.inputTokens + row.outputTokens,
        avgTokens: row.runs > 0 ? (row.inputTokens + row.outputTokens) / row.runs : 0,
        avgCost: row.runs > 0 ? row.cost / row.runs : 0,
      }))
      .sort((left, right) => right.totalTokens - left.totalTokens)
  }

  if (mode === 'user') {
    return groupBy(
      snapshot.runs,
      (run) => run.user_id,
      (run) => ({
        label: run.user_id,
        runs: 1,
        inputTokens: Number(run.prompt_tokens || 0),
        outputTokens: Number(run.completion_tokens || 0),
        cost: Number(run.cost || 0),
        errors: Number(run.errors || 0),
        detail: 'User-triggered usage',
      }),
      (current, run) => ({
        ...current,
        runs: current.runs + 1,
        inputTokens: current.inputTokens + Number(run.prompt_tokens || 0),
        outputTokens: current.outputTokens + Number(run.completion_tokens || 0),
        cost: current.cost + Number(run.cost || 0),
        errors: current.errors + Number(run.errors || 0),
      }),
    )
      .map((row) => ({
        ...row,
        totalTokens: row.inputTokens + row.outputTokens,
        avgTokens: row.runs > 0 ? (row.inputTokens + row.outputTokens) / row.runs : 0,
        avgCost: row.runs > 0 ? row.cost / row.runs : 0,
      }))
      .sort((left, right) => right.cost - left.cost)
  }

  if (mode === 'model' && snapshot.summary?.ai?.model) {
    return [{
      label: snapshot.summary.ai.model,
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      inputTokens: Number(snapshot.stats.total_prompt_tokens || 0),
      outputTokens: Number(snapshot.stats.total_completion_tokens || 0),
      cost: Number(snapshot.stats.total_cost || 0),
      totalTokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      avgTokens: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? (Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0)) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      avgCost: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? Number(snapshot.stats.total_cost || 0) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Single-model footprint visible from the current app summary.',
    }]
  }

  if (mode === 'tool' && Number(snapshot.stats.total_tool_calls || 0) > 0) {
    return [{
      label: 'All tool activity',
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      inputTokens: Number(snapshot.stats.total_prompt_tokens || 0),
      outputTokens: Number(snapshot.stats.total_completion_tokens || 0),
      cost: Number(snapshot.stats.total_cost || 0),
      totalTokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      avgTokens: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? (Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0)) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      avgCost: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? Number(snapshot.stats.total_cost || 0) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Per-tool telemetry has not landed yet, so the app view stays at aggregate tool-call volume.',
    }]
  }

  if (mode === 'agent' && Number(snapshot.stats.total_agent_turns || 0) > 0) {
    return [{
      label: 'App orchestration',
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      inputTokens: Number(snapshot.stats.total_prompt_tokens || 0),
      outputTokens: Number(snapshot.stats.total_completion_tokens || 0),
      cost: Number(snapshot.stats.total_cost || 0),
      totalTokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      avgTokens: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? (Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0)) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      avgCost: (snapshot.stats.tracked_chats || snapshot.runs.length || 0) > 0
        ? Number(snapshot.stats.total_cost || 0) / (snapshot.stats.tracked_chats || snapshot.runs.length)
        : 0,
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Agent detail is still aggregated at the app level in this surface.',
    }]
  }

  return []
}

function exportBreakdownCsv(appId, rows) {
  const headers = ['label', 'runs', 'inputTokens', 'outputTokens', 'totalTokens', 'cost', 'avgTokens', 'avgCost', 'errors', 'detail']
  const lines = [
    headers.join(','),
    ...rows.map((row) => headers.map((header) => JSON.stringify(String(row?.[header] ?? ''))).join(',')),
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

export default function AppUsagePage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const [breakdownMode, setBreakdownMode] = useState('workflow')

  if (loading) return <ConsoleLoadingState label="Loading app usage…" />
  if (error || !data) return <ConsoleErrorState title="Usage Unavailable" message={error || 'No usage data returned.'} />

  const snapshot = getAppConsoleSnapshot(appId, data, dataMode)
  const totalInputTokens = Number(snapshot.stats.total_prompt_tokens || 0)
  const totalOutputTokens = Number(snapshot.stats.total_completion_tokens || 0)
  const totalTokens = Number(snapshot.usageRecord?.tokens_used || 0) || totalInputTokens + totalOutputTokens
  const totalRuns = snapshot.runs.length || snapshot.stats.tracked_chats || 0
  const breakdownRows = buildBreakdownRows(snapshot, breakdownMode)
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
        <AppConsoleHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Usage"
          currentSection="Usage"
          subtitle="Review tokens, cost, workflow runs, and usage drivers."
          summaryItems={summaryItems}
        />

        <Panel
          title="Workflow token breakdown"
          subtitle="Switch views to see what is driving usage."
          action={(
            <div className="flex flex-wrap gap-2">
              <ActionButton
                variant="secondary"
                size="sm"
                disabled={breakdownRows.length === 0}
                onClick={() => exportBreakdownCsv(appId, breakdownRows)}
              >
                Export CSV
              </ActionButton>
            </div>
          )}
        >
          <div className="mb-4">
            <SegmentedControl options={BREAKDOWN_OPTIONS} value={breakdownMode} onChange={setBreakdownMode} />
          </div>

          {breakdownRows.length > 0 ? (
            <div className="overflow-hidden rounded-2xl border border-border/42">
              <table className="min-w-full divide-y divide-border/32 text-sm">
                <thead className="bg-background/34 text-left text-xs text-muted-foreground/84">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Breakdown</th>
                    <th className="px-4 py-3 font-semibold">Runs</th>
                    <th className="px-4 py-3 font-semibold">Input</th>
                    <th className="px-4 py-3 font-semibold">Output</th>
                    <th className="px-4 py-3 font-semibold">Total</th>
                    <th className="px-4 py-3 font-semibold">Avg / Run</th>
                    <th className="px-4 py-3 font-semibold">Cost</th>
                    <th className="px-4 py-3 font-semibold">Errors</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/28 bg-card/24">
                  {breakdownRows.map((row) => (
                    <tr key={row.label}>
                      <td className="px-4 py-3">
                        <div className="font-semibold text-foreground">{row.label}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{row.detail}</div>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.runs, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.inputTokens, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.outputTokens, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.totalTokens, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.avgTokens, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCurrencyValue(row.cost, '$0.00')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.errors, '0')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ConsoleInlineEmptyState
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
              <ConsoleInlineEmptyState
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
                <ConsoleInlineEmptyState
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
