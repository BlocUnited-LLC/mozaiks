import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageFrame } from '@mozaiks/chat-ui'

const DEMO_THREADS = [
  {
    thread_id: 'demo_thread_1',
    title: 'Alex Rivera',
    thread_type: 'dm',
    unread_count: 2,
    last_message: { body_preview: 'Hey, did you see the new build drop?' },
    last_message_at: new Date(Date.now() - 1000 * 60 * 4).toISOString(),
  },
  {
    thread_id: 'demo_thread_2',
    title: 'Jordan Kim',
    thread_type: 'dm',
    unread_count: 0,
    last_message: { body_preview: "Sounds good, let's sync tomorrow" },
    last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString(),
  },
  {
    thread_id: 'demo_thread_3',
    title: 'BlocUnited Team',
    thread_type: 'group',
    unread_count: 5,
    last_message: { body_preview: 'Morgan: shipping the widget fix now' },
    last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString(),
  },
  {
    thread_id: 'demo_thread_4',
    title: 'Sam Osei',
    thread_type: 'dm',
    unread_count: 0,
    last_message: { body_preview: 'Check out this listing I found' },
    last_message_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString(),
  },
]

function formatRelativeTime(iso) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  return `${Math.floor(hrs / 24)}d`
}

export default function MessagesPage() {
  const navigate = useNavigate()
  const totalUnread = DEMO_THREADS.reduce((n, t) => n + (t.unread_count || 0), 0)

  return (
    <PageFrame name="messages" layout="full-width">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 px-4 py-5 sm:px-6">
        <section className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-foreground">
              Messages
              {totalUnread > 0 && (
                <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-bold text-primary-foreground">
                  {totalUnread}
                </span>
              )}
            </h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Direct messages and group conversations.
            </p>
          </div>
        </section>

        <div className="rounded-2xl border border-warning/30 bg-warning/8 px-4 py-2.5 text-xs text-warning/80">
          Demo data — the messages module is a stub in Studio.
        </div>

        <section className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <div className="divide-y divide-border/60">
            {DEMO_THREADS.map((thread) => {
              const initials = (thread.title || 'M').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
              const isGroup = thread.thread_type === 'group'
              return (
                <button
                  key={thread.thread_id}
                  type="button"
                  onClick={() => navigate(`/messages/${thread.thread_id}`)}
                  className="flex w-full items-center gap-4 bg-transparent px-5 py-4 text-left transition hover:bg-muted/40"
                >
                  <span className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-sm font-semibold ${
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
                    {formatRelativeTime(thread.last_message_at)}
                  </span>
                </button>
              )
            })}
          </div>
        </section>
      </div>
    </PageFrame>
  )
}
