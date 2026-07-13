import { useNavigate } from 'react-router-dom'

/**
 * ContactsProfilePanel — profile panel injected by the contacts module.
 * Shown on /profile. Renders the user's contact list with message buttons.
 */

const AVATAR_COLORS = [
  'bg-primary/20 border-primary/30 text-primary',
  'bg-secondary/20 border-secondary/30 text-secondary-foreground',
  'bg-success/20 border-success/30 text-success',
]

function initial(id) {
  return String(id || '?')[0].toUpperCase()
}

export default function ContactsProfilePanel({ data }) {
  const navigate = useNavigate()
  const contacts = Array.isArray(data?.contacts) ? data.contacts : []
  const count = contacts.length

  if (count === 0) {
    return <p className="text-sm text-muted-foreground italic">No contacts yet.</p>
  }

  return (
    <div>
      <div className="divide-y divide-border rounded-xl border border-border">
        {contacts.map((c, i) => (
          <div key={c.contact_id || c.contact_user_id} className="flex items-center justify-between gap-4 px-4 py-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className={`h-8 w-8 rounded-full border flex items-center justify-center flex-shrink-0 ${AVATAR_COLORS[i % AVATAR_COLORS.length]}`}>
                <span className="text-xs font-semibold">{initial(c.contact_user_id)}</span>
              </div>
              <span className="text-sm font-medium text-foreground truncate">{c.contact_user_id}</span>
            </div>
            <button
              type="button"
              onClick={() => navigate('/messages')}
              className="flex-shrink-0 rounded-lg border border-border bg-card px-3 py-1 text-xs font-medium text-foreground transition hover:border-primary/40 hover:text-primary"
            >
              Message
            </button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{count === 1 ? '1 contact' : `${count} contacts`}</span>
        <button type="button" onClick={() => navigate('/messages')} className="text-xs text-primary hover:text-primary/80 transition-colors">
          Open Messages
        </button>
      </div>
    </div>
  )
}
