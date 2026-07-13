import { useNavigate } from 'react-router-dom'

/**
 * ContactsTab — profile tab injected by the contacts module.
 * Shows the contacts/friends roster directly in the profile tab body.
 */

const DEMO_CONTACTS = [
  { contact_id: 'demo_1', contact_user_id: 'alex.rivera', status: 'active', created_at: '2026-07-01T10:00:00Z' },
  { contact_id: 'demo_2', contact_user_id: 'jordan.kim',  status: 'active', created_at: '2026-07-03T14:30:00Z' },
  { contact_id: 'demo_3', contact_user_id: 'sam.osei',    status: 'active', created_at: '2026-07-08T09:15:00Z' },
]

const AVATAR_COLORS = [
  'bg-primary/15 border-primary/30 text-primary',
  'bg-secondary/15 border-secondary/30 text-secondary-foreground',
  'bg-success/15 border-success/30 text-success',
  'bg-warning/15 border-warning/30 text-warning',
]

function initial(id) {
  return String(id || '?')[0].toUpperCase()
}

export default function ContactsTab({ data }) {
  const navigate = useNavigate()
  const contacts = Array.isArray(data?.contacts) ? data.contacts : DEMO_CONTACTS
  const isDemo = !Array.isArray(data?.contacts)

  return (
    <div className="space-y-3">
      {isDemo && (
        <div className="rounded-xl border border-warning/30 bg-warning/8 px-4 py-2 text-xs text-warning/80">
          Demo contacts — add real contacts via the API to replace these.
        </div>
      )}

      {contacts.length === 0 ? (
        <div className="rounded-2xl border border-border bg-card px-6 py-10 text-center">
          <p className="text-sm text-muted-foreground">No contacts yet.</p>
          <p className="mt-1 text-xs text-muted-foreground/70">People you connect with will appear here.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {contacts.map((c, i) => (
            <div
              key={c.contact_id || c.contact_user_id}
              className="flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3"
            >
              <div className={`h-10 w-10 rounded-full border flex items-center justify-center flex-shrink-0 ${AVATAR_COLORS[i % AVATAR_COLORS.length]}`}>
                <span className="text-sm font-semibold">{initial(c.contact_user_id)}</span>
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{c.contact_user_id}</p>
                <p className="text-xs text-muted-foreground">Contact</p>
              </div>
              <button
                type="button"
                onClick={() => navigate('/messages')}
                className="shrink-0 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:border-primary/40 hover:text-primary transition-colors"
              >
                Message
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
