import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  API_BASE,
  ActionButton,
  ConsoleErrorState,
  ConsoleLoadingState,
} from '../../ui/components/ConsoleShared.jsx'


export default function CreateAppRedirectPage() {
  const navigate = useNavigate()
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function createDraftApp() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/apps`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'New App' }),
        })
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        if (!res.ok) throw new Error(body.detail || 'App draft could not be created.')
        const app = body?.app || {}
        if (!cancelled && app.app_id) {
          navigate(`/apps/${encodeURIComponent(app.app_id)}/build`, { replace: true })
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'App draft could not be created.')
        }
      }
    }
    createDraftApp()
    return () => { cancelled = true }
  }, [navigate])

  if (error) {
    return (
      <div className="space-y-4">
        <ConsoleErrorState title="Create App Failed" message={error} />
        <div className="flex justify-center">
          <ActionButton variant="secondary" onClick={() => navigate('/apps')}>
            Return to Apps
          </ActionButton>
        </div>
      </div>
    )
  }

  return <ConsoleLoadingState label="Creating a draft app…" />
}
