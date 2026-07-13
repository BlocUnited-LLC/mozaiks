import { useNavigate } from 'react-router-dom'

/**
 * MessagingProfilePanel — profile panel injected by the messages module.
 * Shown on /profile under the identity section.
 * Renders unread count + link to /messages.
 */
export default function MessagingProfilePanel({ data }) {
  const navigate = useNavigate()
  const unread = Number(data?.unread_thread_count || 0)
  const total = Number(data?.total_thread_count || 0)

  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-4">
        <div className="rounded-xl border border-border bg-background/60 px-4 py-3 text-center min-w-[5rem]">
          <span className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Unread</span>
          <span className={`text-lg font-semibold ${unread > 0 ? 'text-primary' : 'text-foreground'}`}>
            {unread}
          </span>
        </div>
        <div className="rounded-xl border border-border bg-background/60 px-4 py-3 text-center min-w-[5rem]">
          <span className="block text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Threads</span>
          <span className="text-lg font-semibold text-foreground">{total}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={() => navigate('/messages')}
        className="rounded-2xl border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground transition hover:border-primary/40 hover:bg-primary/5 hover:text-primary"
      >
        Open Messages
      </button>
    </div>
  )
}
