import { useCallback, useEffect, useState } from 'react'

import { API_BASE } from './studioApi.js'

export function useWorkspaceApps(errorFallback = 'Workspace apps could not be loaded.') {
  const [apps, setApps] = useState([])
  const [metrics, setMetrics] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode] = useState('live')

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/apps`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setApps(Array.isArray(payload.apps) ? payload.apps : [])
          setMetrics(payload.metrics && typeof payload.metrics === 'object' ? payload.metrics : {})
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : errorFallback)
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

  const deleteApp = useCallback(async (buildRegistryId) => {
    try {
      const res = await fetch(`${API_BASE}/api/studio/apps/${encodeURIComponent(buildRegistryId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      setApps((prev) => prev.filter((a) => a.build_registry_id !== buildRegistryId))
      return true
    } catch (err) {
      console.error('[useWorkspaceApps] deleteApp failed:', err)
      return false
    }
  }, [])

  return {
    apps,
    metrics,
    loading,
    error,
    dataMode,
    deleteApp,
  }
}

export default useWorkspaceApps
