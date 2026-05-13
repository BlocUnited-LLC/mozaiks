import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ActionButton,
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  SegmentedControl,
} from '../../../components/ConsoleShared.jsx'
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

const WINDOW_OPTIONS = [
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
  { value: 'quarter', label: 'Quarter' },
]

function buildBreakdownRows(snapshot, mode) {
  if (mode === 'workflow') {
    return groupBy(
      snapshot.runs,
      (run) => run.workflow_name,
      (run) => ({
        label: run.workflow_name,
        runs: 1,
        cost: Number(run.cost || 0),
        tokens: Number(run.prompt_tokens || 0) + Number(run.completion_tokens || 0),
        errors: Number(run.errors || 0),
        detail: 'Workflow activity',
      }),
      (current, run) => ({
        ...current,
        runs: current.runs + 1,
        cost: current.cost + Number(run.cost || 0),
        tokens: current.tokens + Number(run.prompt_tokens || 0) + Number(run.completion_tokens || 0),
        errors: current.errors + Number(run.errors || 0),
      }),
    ).sort((left, right) => right.runs - left.runs)
  }

  if (mode === 'user') {
    return groupBy(
      snapshot.runs,
      (run) => run.user_id,
      (run) => ({
        label: run.user_id,
        runs: 1,
        cost: Number(run.cost || 0),
        tokens: Number(run.prompt_tokens || 0) + Number(run.completion_tokens || 0),
        errors: Number(run.errors || 0),
        detail: 'User-triggered usage',
      }),
      (current, run) => ({
        ...current,
        runs: current.runs + 1,
        cost: current.cost + Number(run.cost || 0),
        tokens: current.tokens + Number(run.prompt_tokens || 0) + Number(run.completion_tokens || 0),
        errors: current.errors + Number(run.errors || 0),
      }),
    ).sort((left, right) => right.cost - left.cost)
  }

  if (mode === 'model' && snapshot.summary?.ai?.model) {
    return [{
      label: snapshot.summary.ai.model,
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      cost: Number(snapshot.stats.total_cost || 0),
      tokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Single-model footprint visible from the current app summary.',
    }]
  }

  if (mode === 'tool' && Number(snapshot.stats.total_tool_calls || 0) > 0) {
    return [{
      label: 'All tool activity',
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      cost: Number(snapshot.stats.total_cost || 0),
      tokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Per-tool telemetry has not landed yet, so the app view stays at aggregate tool-call volume.',
    }]
  }

  if (mode === 'agent' && Number(snapshot.stats.total_agent_turns || 0) > 0) {
    return [{
      label: 'App orchestration',
      runs: snapshot.stats.tracked_chats || snapshot.runs.length,
      cost: Number(snapshot.stats.total_cost || 0),
      tokens: Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0),
      errors: Number(snapshot.stats.total_errors || 0),
      detail: 'Agent detail is still aggregated at the app level in this surface.',
    }]
  }

  return []
}

function exportBreakdownCsv(appId, rows) {
  const headers = ['label', 'runs', 'cost', 'tokens', 'errors', 'detail']
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
  const [timeWindow, setTimeWindow] = useState('30d')

  if (loading) return <ConsoleLoadingState label="Loading app usage…" />
  if (error || !data) return <ConsoleErrorState title="Usage Unavailable" message={error || 'No usage data returned.'} />

  const snapshot = getAppConsoleSnapshot(appId, data, dataMode)
  const totalTokens = Number(snapshot.usageRecord?.tokens_used || 0) || Number(snapshot.stats.total_prompt_tokens || 0) + Number(snapshot.stats.total_completion_tokens || 0)
  const apiRequests = snapshot.sessions.length || snapshot.runs.length || snapshot.stats.tracked_chats || 0
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
    { id: 'tokens', label: 'Tokens Used', value: formatCompactNumber(totalTokens, 'Pending'), detail: 'Prompt and completion traffic.' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(snapshot.usageRecord?.llm_cost_usd ?? snapshot.stats.total_cost), detail: 'Observed model cost.' },
    { id: 'requests', label: 'API Requests', value: formatCompactNumber(apiRequests, '0'), detail: 'Tracked app sessions and requests.' },
    { id: 'tools', label: 'Tool Calls', value: formatCompactNumber(snapshot.stats.total_tool_calls, '0'), detail: 'Runtime tool invocations.' },
    { id: 'errors', label: 'Errors', value: formatCompactNumber(snapshot.stats.total_errors, '0'), detail: 'Runtime or workflow errors.' },
  ]

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Usage"
          currentSection="Usage"
          subtitle="Keep usage app-scoped and readable: costs, runtime demand, breakdowns, and the signals that explain where spend or errors are coming from."
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Usage breakdown"
          title="Breakdown by app surface"
          subtitle="Switch the lens to see which part of the app is driving usage, cost, and errors."
          action={(
            <div className="flex flex-wrap gap-2">
              <SegmentedControl options={WINDOW_OPTIONS} value={timeWindow} onChange={setTimeWindow} />
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
            <div className="overflow-hidden rounded-2xl border border-border">
              <table className="min-w-full divide-y divide-border text-sm">
                <thead className="bg-background/80 text-left text-xs uppercase tracking-[0.16em] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Breakdown</th>
                    <th className="px-4 py-3 font-semibold">Runs</th>
                    <th className="px-4 py-3 font-semibold">Tokens</th>
                    <th className="px-4 py-3 font-semibold">Cost</th>
                    <th className="px-4 py-3 font-semibold">Errors</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border bg-card/60">
                  {breakdownRows.map((row) => (
                    <tr key={row.label}>
                      <td className="px-4 py-3">
                        <div className="font-semibold text-foreground">{row.label}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{row.detail}</div>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.runs, '0')}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatCompactNumber(row.tokens, '0')}</td>
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
              description={`The ${timeWindow.toUpperCase()} reporting view will become more informative once this app has runtime traffic beyond early Build activity.`}
            />
          )}
        </Panel>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Panel eyebrow="Cost drivers" title="Highest-cost activity" subtitle="Use this short list to see which workflows are driving the current usage bill.">
            {costDrivers.length > 0 ? (
              <div className="space-y-3">
                {costDrivers.map((run) => (
                  <div key={run.chat_id} className="rounded-2xl border border-border bg-card/70 px-4 py-3">
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
            <Panel eyebrow="Error trends" title="Runtime error posture" subtitle="Keep the app usage screen practical by surfacing only the workflows that are contributing the most friction.">
              {errorRows.length > 0 ? (
                <div className="space-y-3">
                  {errorRows.map((run) => (
                    <div key={run.chat_id} className="rounded-2xl border border-border bg-card/70 px-4 py-3">
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

            <Panel eyebrow="Latency trends" title="Response-time posture" subtitle="Latency is intentionally high-level here so operators can spot drift without opening raw traces.">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Average latency</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{averageLatency != null ? `${averageLatency}s` : 'Pending'}</div>
                </div>
                <div className="rounded-2xl border border-border bg-card/70 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Tracked workflows</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{formatCompactNumber(snapshot.workflowNames.length, '0')}</div>
                </div>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}
