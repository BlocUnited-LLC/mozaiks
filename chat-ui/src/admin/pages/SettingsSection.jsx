import { useChatUI } from '../../context/ChatUIContext'
import {
  SectionFrame,
  SectionHeading,
  EmptyState,
  AdminExtensionPanels,
} from '../components/AdminPrimitives.jsx'

function ConfigRow({ label, value, mono = true }) {
  return (
    <div className="rounded-lg border border-border bg-background px-4 py-3">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={`text-sm text-foreground truncate ${mono ? 'font-mono' : 'font-medium'}`}>
        {value || <span className="text-muted-foreground italic">not set</span>}
      </div>
    </div>
  )
}

export function SettingsSection({ extensionPanels }) {
  const { config } = useChatUI()

  const appId = config?.chat?.defaultAppId || config?.appId || config?.app_id || null
  const appName = config?.appName || config?.app_name || null
  const backendUrl = config?.appBackendUrl || config?.app_backend_url || null
  const apiUrl =
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
    ''
  const displayedApiUrl = apiUrl || 'same-origin'

  return (
    <SectionFrame
      title="Settings"
      description="App configuration, branding, auth, domains, and environment controls."
    >
      <SectionHeading>Runtime Configuration</SectionHeading>
      <div className="grid gap-3 sm:grid-cols-2">
        <ConfigRow label="App Name"    value={appName}     mono={false} />
        <ConfigRow label="App ID"      value={appId} />
        <ConfigRow label="Runtime API" value={displayedApiUrl} />
        <ConfigRow label="Backend URL" value={backendUrl} />
      </div>

      {extensionPanels.length > 0 && (
        <>
          <SectionHeading>App Settings</SectionHeading>
          <AdminExtensionPanels panels={extensionPanels} />
        </>
      )}

      {extensionPanels.length === 0 && (
        <div className="mt-4">
          <EmptyState>
            No app settings panels configured. Declare settings panels in your module's{' '}
            <code className="font-mono text-xs">admin.yaml</code> and register them via the{' '}
            <code className="font-mono text-xs">settings</code> section.
          </EmptyState>
        </div>
      )}
    </SectionFrame>
  )
}
