import { useEffect, useState } from 'react'

import { API_BASE } from './consoleApi.js'
import {
  buildConsoleDemoApps,
  getConsoleDemoWorkspaceRuns,
  getConsoleDemoWorkspaceStats,
  isConsoleDemoModeEnabled,
} from './consoleDemoData.js'

export function useWorkspaceConsoleData(errorFallback = 'Workspace console data could not be loaded.') {
  const [apps, setApps] = useState([])
  const [metrics, setMetrics] = useState({})
  const [workspaceStats, setWorkspaceStats] = useState({})
  const [workspaceRuns, setWorkspaceRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')

  useEffect(() => {
    let cancelled = false
    const demoMode = isConsoleDemoModeEnabled()

    async function load() {
      try {
        const [appsRes, statsRes, runsRes] = await Promise.allSettled([
          fetch(`${API_BASE}/api/studio/apps`),
          fetch(`${API_BASE}/api/admin/stats`),
          fetch(`${API_BASE}/api/admin/runs?limit=48`),
        ])

        const appsPayload = appsRes.status === 'fulfilled' && appsRes.value.ok ? await appsRes.value.json() : null

        if (!appsPayload) {
          throw new Error(errorFallback)
        }

        const statsPayload = statsRes.status === 'fulfilled' && statsRes.value.ok ? await statsRes.value.json() : null
        const runsPayload = runsRes.status === 'fulfilled' && runsRes.value.ok ? await runsRes.value.json() : null
        const liveApps = Array.isArray(appsPayload.apps) ? appsPayload.apps : []
        const useDemoApps = demoMode && liveApps.length === 0

        if (!cancelled) {
          setApps(useDemoApps ? buildConsoleDemoApps() : liveApps)
          setMetrics(appsPayload.metrics && typeof appsPayload.metrics === 'object' ? appsPayload.metrics : {})
          setWorkspaceStats(
            useDemoApps
              ? getConsoleDemoWorkspaceStats()
              : statsPayload && typeof statsPayload === 'object'
                ? statsPayload
                : {},
          )
          setWorkspaceRuns(
            useDemoApps
              ? getConsoleDemoWorkspaceRuns()
              : Array.isArray(runsPayload?.runs)
                ? runsPayload.runs
                : [],
          )
          setDataMode(useDemoApps ? 'demo' : 'live')
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          if (demoMode) {
            setApps(buildConsoleDemoApps())
            setMetrics({})
            setWorkspaceStats(getConsoleDemoWorkspaceStats())
            setWorkspaceRuns(getConsoleDemoWorkspaceRuns())
            setDataMode('demo')
            setError(null)
          } else {
            setError(err instanceof Error ? err.message : errorFallback)
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [errorFallback])

  return {
    apps,
    metrics,
    workspaceStats,
    workspaceRuns,
    loading,
    error,
    dataMode,
  }
}

export default useWorkspaceConsoleData