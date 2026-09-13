import { useEffect, useState } from 'react'

import { studioFetch } from './studioApi.js'
import {
  getStudioDemoAnalyticsApp,
  getStudioDemoAnalyticsPortfolio,
  getStudioDemoMetricDetail,
  isStudioDemoApp,
  isStudioDemoModeEnabled,
  isStudioUsageDemoModeEnabled,
} from './studioDemoData.js'

function demoModeActive() {
  return isStudioDemoModeEnabled() || isStudioUsageDemoModeEnabled()
}

/** World View analytics: /api/studio/analytics/portfolio */
export function usePortfolioAnalytics(period) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        if (isStudioUsageDemoModeEnabled()) {
          if (!cancelled) {
            setData(getStudioDemoAnalyticsPortfolio(period))
            setDataMode('demo')
            setError(null)
          }
          return
        }
        const response = await studioFetch(
          `/api/studio/analytics/portfolio?period=${encodeURIComponent(period)}`,
        )
        if (!response.ok) throw new Error('Portfolio analytics unavailable.')
        const payload = await response.json()
        if (!cancelled) {
          setData(payload)
          setDataMode('live')
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          if (demoModeActive()) {
            setData(getStudioDemoAnalyticsPortfolio(period))
            setDataMode('demo')
            setError(null)
          } else {
            setError(err instanceof Error ? err.message : 'Portfolio analytics unavailable.')
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
  }, [period, refreshKey])

  return { data, loading, error, dataMode, refresh: () => setRefreshKey((key) => key + 1) }
}

/** App View analytics: /api/studio/analytics/apps/{appId} */
export function useAppAnalytics(appId, period) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        if (isStudioUsageDemoModeEnabled() && isStudioDemoApp(appId)) {
          if (!cancelled) {
            setData(getStudioDemoAnalyticsApp(appId, period))
            setDataMode('demo')
            setError(null)
          }
          return
        }
        const response = await studioFetch(
          `/api/studio/analytics/apps/${encodeURIComponent(appId)}?period=${encodeURIComponent(period)}`,
        )
        if (!response.ok) throw new Error('App analytics unavailable.')
        const payload = await response.json()
        if (!cancelled) {
          setData(payload)
          setDataMode('live')
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          if (demoModeActive() && isStudioDemoApp(appId)) {
            setData(getStudioDemoAnalyticsApp(appId, period))
            setDataMode('demo')
            setError(null)
          } else {
            setError(err instanceof Error ? err.message : 'App analytics unavailable.')
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
  }, [appId, period, refreshKey])

  return { data, loading, error, dataMode, refresh: () => setRefreshKey((key) => key + 1) }
}

/** Metric drill-down: /api/studio/analytics/apps/{appId}/metrics/{metricId} */
export async function fetchMetricDetail(appId, metricId, period) {
  if (isStudioUsageDemoModeEnabled() && isStudioDemoApp(appId)) {
    return { payload: getStudioDemoMetricDetail(appId, metricId, period), dataMode: 'demo' }
  }
  try {
    const response = await studioFetch(
      `/api/studio/analytics/apps/${encodeURIComponent(appId)}/metrics/${encodeURIComponent(metricId)}?period=${encodeURIComponent(period)}`,
    )
    if (!response.ok) throw new Error('Metric detail unavailable.')
    return { payload: await response.json(), dataMode: 'live' }
  } catch (err) {
    if (demoModeActive() && isStudioDemoApp(appId)) {
      return { payload: getStudioDemoMetricDetail(appId, metricId, period), dataMode: 'demo' }
    }
    throw err
  }
}
