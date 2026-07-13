import { useNavigate } from 'react-router-dom'

/**
 * MessagingTab — profile tab injected by the messages module.
 * Shows thread list with unread badges directly in the profile tab body.
 */

const DEMO_THREADS = [
  { thread_id: 'demo_thread_1', title: 'Alex Rivera',     thread_type: 'dm',    unread_count: 2, last_message: { body_preview: 'Hey, did you see the new build drop?' },     last_message_at: new Date(Date.now() - 1000 * 60 * 4).toISOString() },
  { thread_id: 'demo_thread_2', title: 'Jordan Kim',      thread_type: 'dm',    unread_count: 0, last_message: { body_preview: "Sounds good, let's sync tomorrow" },          last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString() },
  { thread_id: 'demo_thread_3', title: 'BlocUnited Team', thread_type: 'group', unread_count: 5, last_message: { body_preview: 'Morgan: shipping the widget fix now' },       last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString() },
  { thread_id: 'demo_thread_4', title: 'Sam Osei',        thread_type: 'dm',    unread_count: 0, last_message: { body_preview: 'Check out this listing I found' },            last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString() },
]

function relativeTime(iso) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

export default function MessagingTab({ data }) {
  const navigate = useNavigate()
  const threads = Array.isArray(data?.threads) ? data.threads : DEMO_THREADS
  const isDemo = !Array.isArray(data?.threads)

  return (
    <div className="space-y-3">
      {isDemo && (
        <div className="rounded-xl border border-warning/30 bg-warning/8 px-4 py-2 text-xs text-warning/80">
          Demo threads — connect the messages module to see real conversations.
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="divide-y divide-border/60">
          {threads.map((thread) => {
            const initials = (thread.title || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
            const isGroup = thread.thread_type === 'group'
            return (
              <button
                key={thread.thread_id}
                type="button"
                onClick={() => navigate(`/messages/${thread.thread_id}`)}
                className="flex w-full items-center gap-3 bg-transparent px-4 py-3.5 text-left transition hover:bg-muted/40"
              >
                <span className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl text-xs font-semibold ${
                  isGroup
                    ? 'border border-secondary/30 bg-secondary/15 text-secondary-foreground'
                    : 'border border-primary/20 bg-primary/10 text-primary'
                }`}>
                  {initials}
                  {thread.unread_count > 0 && (
                    <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold text-primary-foreground">
                      {thread.unread_count}
                    </span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={`block truncate text-sm ${thread.unread_count > 0 ? 'font-semibold text-foreground' : 'font-medium text-foreground/90'}`}>
                    {thread.title}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {thread.last_message?.body_preview || 'No messages yet'}
                  </span>
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground/60">
                  {relativeTime(thread.last_message_at)}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <button
        type="button"
        onClick={() => navigate('/messages')}
        className="w-full rounded-2xl border border-border bg-card py-3 text-sm font-medium text-muted-foreground hover:text-foreground hover:border-primary/30 transition-colors"
      >
        Open Messages
      </button>
    </div>
  )
}
