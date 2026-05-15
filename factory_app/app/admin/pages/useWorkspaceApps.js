import { useEffect, useState } from 'react'

import { API_BASE } from './consoleApi.js'
import { buildConsoleDemoApps, isConsoleDemoModeEnabled } from './consoleDemoData.js'

export function useWorkspaceApps(errorFallback = 'Workspace apps could not be loaded.') {
  const [apps, setApps] = useState([])
  const [metrics, setMetrics] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')

  useEffect(() => {
    let cancelled = false
    const demoMode = isConsoleDemoModeEnabled()

    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/apps`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        const liveApps = Array.isArray(payload.apps) ? payload.apps : []

        if (!cancelled) {
          const useDemoApps = demoMode && liveApps.length === 0
          setApps(useDemoApps ? buildConsoleDemoApps() : liveApps)
          setMetrics(payload.metrics && typeof payload.metrics === 'object' ? payload.metrics : {})
          setDataMode(useDemoApps ? 'demo' : 'live')
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          if (demoMode) {
            setApps(buildConsoleDemoApps())
            setMetrics({})
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
    loading,
    error,
    dataMode,
  }
}

export default useWorkspaceApps
