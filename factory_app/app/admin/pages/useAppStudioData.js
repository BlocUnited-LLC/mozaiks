import { useEffect, useState } from 'react'

import { API_BASE } from './studioApi.js'
import {
  buildStudioDemoAppSummary,
  getStudioDemoActivity,
  getStudioDemoAdminStats,
  getStudioDemoBuildHistory,
  getStudioDemoRuns,
  getStudioDemoSessions,
  getStudioDemoUsagePayload,
  getStudioDemoWorkflowNames,
  isStudioDemoApp,
  isStudioDemoModeEnabled,
  isStudioUsageDemoModeEnabled,
} from './studioDemoData.js'

function buildDemoBuildState(appId, summary) {
  const lifecycleState = summary?.app?.lifecycle_state || 'draft'
  const workflowNames = getStudioDemoWorkflowNames(appId)
  const baseBrief =
    lifecycleState === 'active'
      ? 'Refine the live app without disrupting runtime operations.'
      : lifecycleState === 'deploying'
        ? 'Complete the release checks and final deployment tasks.'
        : 'Continue shaping the app brief and scoped implementation plan.'

  return {
    current_request: {
      text: baseBrief,
      request_kind: lifecycleState === 'draft' ? 'greenfield_app' : 'refinement',
      change_class: lifecycleState === 'needs_revision' ? 'patch' : 'feature',
      updated_at: summary?.app?.updated_at || summary?.app?.created_at || null,
    },
    current_plan: {
      summary: lifecycleState === 'active'
        ? 'Live app changes must preserve runtime stability while expanding product capability.'
        : 'Build is still shaping the active request into a reviewable app bundle.',
      build_tasks: [],
      owned_paths: ['app/modules', 'app/ui/pages', 'app/config'],
      acceptance_criteria: [],
      approvals_required: [],
      cost_implications: [],
      runtime_implications: [],
    },
    recent_requests: [
      {
        text: baseBrief,
        request_kind: lifecycleState === 'draft' ? 'greenfield_app' : 'refinement',
        change_class: lifecycleState === 'needs_revision' ? 'patch' : 'feature',
        saved_at: summary?.app?.updated_at || summary?.app?.created_at || null,
      },
    ],
    plan_state: lifecycleState === 'draft' ? 'draft_saved' : 'plan_ready',
    approval_state:
      lifecycleState === 'review' || lifecycleState === 'configuring'
        ? 'pending'
        : lifecycleState === 'needs_revision'
          ? 'rejected'
          : 'approved',
    last_saved_at: summary?.app?.updated_at || summary?.app?.created_at || null,
    supports_initial_compile: true,
    initial_compile_workflow: workflowNames[0] || 'AppGenerator',
    refinement_support: {
      patch: { available: true, workflow_id: 'AppGenerator' },
      design: { available: true, workflow_id: 'DesignDocs' },
      feature: { available: true, workflow_id: 'AppGenerator' },
      core: { available: true, workflow_id: 'ValueEngine' },
    },
    app_validation: {
      default_value: 'standard',
      options: [
        { value: 'standard', label: 'Standard', description: 'Run the default validation checks.' },
      ],
    },
  }
}

function buildDemoPayload(appId) {
  const summary = buildStudioDemoAppSummary(appId)
  return {
    summary,
    stats: getStudioDemoAdminStats(appId),
    runs: { runs: getStudioDemoRuns(appId), total: getStudioDemoRuns(appId).length },
    sessions: { sessions: getStudioDemoSessions(appId), total: getStudioDemoSessions(appId).length },
    usage: getStudioDemoUsagePayload(appId),
    buildState: { build: buildDemoBuildState(appId, summary) },
    buildHistory: getStudioDemoBuildHistory(appId),
    integrations: null,
    activity: getStudioDemoActivity(appId),
  }
}

export function useAppStudioData(appId) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const demoMode = isStudioDemoModeEnabled()
    const usageDemoMode = isStudioUsageDemoModeEnabled()

    async function load() {
      try {
        if (usageDemoMode && isStudioDemoApp(appId)) {
          if (!cancelled) {
            setData(buildDemoPayload(appId))
            setDataMode('demo')
            setError(null)
          }
          return
        }

        const [
          overviewRes,
          statsRes,
          runsRes,
          sessionsRes,
          usageRes,
          buildRes,
          historyRes,
          integrationsRes,
          runtimeMetricsRes,
        ] = await Promise.allSettled([
          fetch(`${API_BASE}/api/studio/overview?app_id=${encodeURIComponent(appId)}`),
          fetch(`${API_BASE}/api/admin/stats?app_id=${encodeURIComponent(appId)}`),
          fetch(`${API_BASE}/api/admin/runs?app_id=${encodeURIComponent(appId)}&limit=12`),
          fetch(`${API_BASE}/api/admin/sessions?app_id=${encodeURIComponent(appId)}&limit=12`),
          fetch(`${API_BASE}/api/admin/usage?app_id=${encodeURIComponent(appId)}&limit=500`),
          fetch(`${API_BASE}/api/studio/build?app_id=${encodeURIComponent(appId)}`),
          fetch(`${API_BASE}/api/studio/build/history?app_id=${encodeURIComponent(appId)}&limit=8`),
          fetch(`${API_BASE}/api/studio/integrations?app_id=${encodeURIComponent(appId)}`),
          fetch(`${API_BASE}/api/admin/metrics/runtime`),
        ])

        const overview = overviewRes.status === 'fulfilled' && overviewRes.value.ok ? await overviewRes.value.json() : null
        const stats = statsRes.status === 'fulfilled' && statsRes.value.ok ? await statsRes.value.json() : null
        const runs = runsRes.status === 'fulfilled' && runsRes.value.ok ? await runsRes.value.json() : null
        const sessions = sessionsRes.status === 'fulfilled' && sessionsRes.value.ok ? await sessionsRes.value.json() : null
        const usage = usageRes.status === 'fulfilled' && usageRes.value.ok ? await usageRes.value.json() : null
        const buildState = buildRes.status === 'fulfilled' && buildRes.value.ok ? await buildRes.value.json() : null
        const buildHistory = historyRes.status === 'fulfilled' && historyRes.value.ok ? await historyRes.value.json() : null
        const integrations = integrationsRes.status === 'fulfilled' && integrationsRes.value.ok ? await integrationsRes.value.json() : null
        const runtimeMetrics = runtimeMetricsRes.status === 'fulfilled' && runtimeMetricsRes.value.ok ? await runtimeMetricsRes.value.json() : null

        if (!overview) {
          throw new Error('App summary unavailable.')
        }

        if (!cancelled) {
          setData({
            summary: overview,
            stats,
            runs,
            sessions,
            usage,
            buildState,
            buildHistory,
            integrations,
            runtimeMetrics,
            activity: [],
          })
          setDataMode('live')
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          if (demoMode) {
            setData(buildDemoPayload(appId))
            setDataMode('demo')
            setError(null)
          } else {
            setError(err instanceof Error ? err.message : 'App Studio data could not be loaded.')
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    setLoading(true)
    load()
    return () => {
      cancelled = true
    }
  }, [appId, refreshKey])

  return {
    data,
    loading,
    error,
    dataMode,
    refresh: () => setRefreshKey((k) => k + 1),
  }
}

export default useAppStudioData
