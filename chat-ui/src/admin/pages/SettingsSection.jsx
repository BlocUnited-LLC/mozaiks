import { useChatUI } from '../../context/ChatUIContext'
import { SectionHeading } from '../components/AdminPrimitives.jsx'

function ConfigRow({ label, value, emptyLabel = 'Not set', mono = true }) {
  return (
    <div className="rounded-lg border border-border bg-background px-4 py-3">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={`text-sm text-foreground truncate ${mono ? 'font-mono' : 'font-medium'}`}>
        {value || <span className="text-muted-foreground italic">{emptyLabel}</span>}
      </div>
    </div>
  )
}

/**
 * Settings — the values this app is running with. Feature settings are panels
 * that modules declare on the `settings` page; the page shell renders them.
 */
export function SettingsSection() {
  const { config } = useChatUI()

  const appId = config?.chat?.defaultAppId || config?.appId || config?.app_id || null
  const appName = config?.appName || config?.app_name || null
  const backendUrl = config?.appBackendUrl || config?.app_backend_url || null
  const apiUrl =
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
    ''

  return (
    <section>
      <SectionHeading>App details</SectionHeading>
      <div className="grid gap-3 sm:grid-cols-2">
        <ConfigRow label="App name" value={appName} mono={false} />
        <ConfigRow label="App ID" value={appId} />
        <ConfigRow label="API address" value={apiUrl} emptyLabel="Same address as this site" />
        <ConfigRow label="Connected backend" value={backendUrl} emptyLabel="None" />
      </div>
    </section>
  )
}
