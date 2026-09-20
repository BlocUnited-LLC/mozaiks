import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { Alert, UsageTrendPanel } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
  SegmentedControl,
} from '../../ui/components/StudioShared.jsx'
import { AppStudioHero, formatCompactNumber, formatCurrencyValue } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot, sumBy } from './appStudioDataHelpers.js'
import PricingHealthPanel from './PricingHealthPanel.jsx'
import {
  USAGE_TREND_METRICS,
  buildUsageTrendSeries,
  getUsageRows,
} from './usagePresentation.js'
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

function WorkflowGroupRow({ group, expanded, onToggle }) {
  return (
    <>
      <tr onClick={onToggle} className="border-b border-border cursor-pointer hover:bg-muted/25 transition-colors">
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] text-muted-foreground transition-transform duration-150 select-none ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <span className="font-semibold text-foreground text-sm">{group.workflow}</span>
            <span className="text-xs text-muted-foreground ml-1">{group.count} chat{group.count !== 1 ? 's' : ''}</span>
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
  const [chartMetric, setChartMetric] = useState('cost')

  const toggleExpand = (workflowName) => {
    setExpandedWorkflows((prev) => {
      const next = new Set(prev)
      next.has(workflowName) ? next.delete(workflowName) : next.add(workflowName)
      return next
    })
  }

  if (loading) return <StudioLoadingState label="Loading app usage..." />
  if (error || !data) return <StudioErrorState title="Token Usage Unavailable" message={error || 'No usage data returned.'} />

  const snapshot = getAppStudioSnapshot(appId, data, dataMode)
  const usageTotals = snapshot.usage?.totals || {}
  const workflowGroups = buildWorkflowGroups(snapshot.usage, dataMode === 'demo' ? snapshot.runs : [])
  const totalInputTokens = Number(usageTotals.prompt_tokens || 0)
  const totalOutputTokens = Number(usageTotals.completion_tokens || 0)
  const totalTokens = Number(usageTotals.total_tokens || 0) || totalInputTokens + totalOutputTokens
  const totalCost = Number(usageTotals.estimated_cost_usd || 0)
  const totalRuns = Number(snapshot.stats.tracked_chats || 0)
  const totalLlmCalls = Number(usageTotals.llm_calls || 0)
  const usageRows = getUsageRows(snapshot.usage, dataMode === 'demo' ? snapshot.runs : [])
  const trendData = buildUsageTrendSeries(usageRows, chartMetric).map((point) => ({
    ...point,
    extras: [
      { label: 'Date', value: point.detail || point.label },
      { label: 'Cost', value: formatCurrencyValue(point.cost, '$0.0000') },
      { label: 'Tokens', value: formatCompactNumber(point.tokens, '0') },
      { label: 'Chats', value: String(point.runs) },
    ],
  }))
  const averageLatency = snapshot.runs.length > 0
    ? Math.round(sumBy(snapshot.runs, (run) => run.runtime_sec || 0) / snapshot.runs.length)
    : null

  const costSource = snapshot.usage?.cost_source
  const pricingHealth = snapshot.usage?.pricing_health
  const costDetail = pricingHealth?.status === 'ready'
    ? 'Catalog priced'
    : pricingHealth?.status === 'unpriced_models'
      ? 'Unpriced models'
      : costSource === 'default_table'
        ? 'Fallback list prices'
        : costSource === 'override'
          ? 'Override rates'
          : 'Estimated or provider supplied'
  const appDisplayName = snapshot.app?.name || snapshot.summary?.app?.name || appId
  const chartConfig = {
    cost: {
      label: 'Total spend',
      value: formatCurrencyValue(totalCost),
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
    { id: 'calls', label: 'LLM calls', value: formatCompactNumber(totalLlmCalls, '0') },
    {
      id: 'workflows',
      label: 'Tracked workflows',
      value: formatCompactNumber(snapshot.workflowNames.length, '0'),
      detail: averageLatency != null ? `${averageLatency}s average runtime` : 'Runtime appears after completed chats.',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Token Usage"
          currentSection="Token Usage"
          subtitle="Measured runtime token usage and cost estimates for this app."
          actions={null}
          onAction={null}
        />

        <UsageTrendPanel
          title={`${appDisplayName} usage`}
          subtitle="Spend, token volume, and chat activity for this app."
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

        <PricingHealthPanel usage={snapshot.usage} />

        <Panel title="Workflow breakdown" subtitle="Expand a workflow to see metered chats.">
          {workflowGroups.length > 0 ? (
            <div className="overflow-hidden rounded-2xl border border-border/42">
              <table className="min-w-full text-sm">
                <thead className="bg-background/34 border-b border-border/42">
                  <tr>
                    <th className={COL_HEADER}>Workflow</th>
                    <th className={COL_HEADER}>Input</th>
                    <th className={COL_HEADER}>Output</th>
                    <th className={COL_HEADER}>Total</th>
                    <th className={COL_HEADER}>Avg / chat</th>
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
              description="This view becomes informative once AG2 1.0 workflow agents produce metered LLM calls."
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
    </WorkspaceLayout>
  )
}
