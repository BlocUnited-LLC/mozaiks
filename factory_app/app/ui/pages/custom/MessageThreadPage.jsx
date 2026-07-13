import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { PageFrame } from '@mozaiks/chat-ui'

const DEMO_THREADS = {
  demo_thread_1: {
    thread: { thread_id: 'demo_thread_1', title: 'Alex Rivera', thread_type: 'dm' },
    messages: [
      { message_id: 'd1m1', sender_id: 'alex',   body: 'Hey! Did you see the new build drop?',                       created_at: new Date(Date.now() - 1000 * 60 * 14).toISOString() },
      { message_id: 'd1m2', sender_id: '__me__', body: 'Just pulled it. The widget changes look clean.',             created_at: new Date(Date.now() - 1000 * 60 * 10).toISOString() },
      { message_id: 'd1m3', sender_id: 'alex',   body: 'Yeah the rating banner was a nice touch. Adding more card types?', created_at: new Date(Date.now() - 1000 * 60 * 4).toISOString() },
    ],
  },
  demo_thread_2: {
    thread: { thread_id: 'demo_thread_2', title: 'Jordan Kim', thread_type: 'dm' },
    messages: [
      { message_id: 'd2m1', sender_id: '__me__', body: 'Are you free tomorrow to sync on the marketplace work?',    created_at: new Date(Date.now() - 1000 * 60 * 60 * 3).toISOString() },
      { message_id: 'd2m2', sender_id: 'jordan', body: "Sounds good, let's sync tomorrow. Morning works for me.",   created_at: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString() },
    ],
  },
  demo_thread_3: {
    thread: { thread_id: 'demo_thread_3', title: 'BlocUnited Team', thread_type: 'group' },
    messages: [
      { message_id: 'd3m1', sender_id: 'sam',    body: 'Good morning everyone',                                     created_at: new Date(Date.now() - 1000 * 60 * 60 * 6).toISOString() },
      { message_id: 'd3m2', sender_id: '__me__', body: 'Morning! Pushing a few fixes today.',                       created_at: new Date(Date.now() - 1000 * 60 * 60 * 5.5).toISOString() },
      { message_id: 'd3m3', sender_id: 'morgan', body: 'On it too — shipping the widget fix now',                   created_at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString() },
      { message_id: 'd3m4', sender_id: 'alex',   body: "Let's do a quick check-in at 3?",                          created_at: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString() },
      { message_id: 'd3m5', sender_id: '__me__', body: 'Works for me',                                             created_at: new Date(Date.now() - 1000 * 60 * 60 * 3.5).toISOString() },
    ],
  },
  demo_thread_4: {
    thread: { thread_id: 'demo_thread_4', title: 'Sam Osei', thread_type: 'dm' },
    messages: [
      { message_id: 'd4m1', sender_id: 'sam',    body: 'Check out this listing I found on the marketplace',         created_at: new Date(Date.now() - 1000 * 60 * 60 * 25).toISOString() },
      { message_id: 'd4m2', sender_id: '__me__', body: 'Nice find. The revenue participation looks solid.',         created_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString() },
    ],
  },
}

export default function MessageThreadPage() {
  const { threadId } = useParams()
  const demo = DEMO_THREADS[threadId] || null
  const [messages, setMessages] = useState(demo?.messages || [])
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const listRef = useRef(null)
  const thread = demo?.thread || null
  const threadTitle = thread?.title || 'Conversation'
  const isGroup = thread?.thread_type === 'group'

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages.length])

  function sendMessage(event) {
    event.preventDefault()
    const text = body.trim()
    if (!text) return
    setSending(true)
    setMessages((prev) => [...prev, {
      message_id: `sent_${Date.now()}`,
      sender_id: '__me__',
      body: text,
      created_at: new Date().toISOString(),
    }])
    setBody('')
    setSending(false)
  }

  if (!demo) {
    return (
      <PageFrame name="message-thread" layout="full-width">
        <div className="mx-auto max-w-2xl px-4 py-8">
          <Link to="/messages" className="text-sm text-primary hover:underline">← Back to messages</Link>
          <p className="mt-4 text-sm text-muted-foreground">Thread not found.</p>
        </div>
      </PageFrame>
    )
  }

  return (
    <PageFrame name="message-thread" layout="full-width">
      <div className="mx-auto flex min-h-0 w-full max-w-2xl flex-1 flex-col px-4 py-4 sm:px-6">

        {/* Header */}
        <div className="mb-3 flex items-center gap-3">
          <Link to="/messages" className="shrink-0 rounded-xl border border-border/60 bg-card px-3 py-1.5 text-xs font-semibold text-muted-foreground transition hover:border-border hover:text-foreground">
            ← Back
          </Link>
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-xs font-semibold ${
              isGroup
                ? 'border border-secondary/30 bg-secondary/15 text-secondary-foreground'
                : 'border border-primary/20 bg-primary/10 text-primary'
            }`}>
              {threadTitle.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-foreground">{threadTitle}</h1>
              {isGroup && <p className="text-xs text-muted-foreground">Group conversation</p>}
            </div>
          </div>
          <span className="shrink-0 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[10px] text-warning/80">Demo</span>
        </div>

        {/* Messages + compose */}
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <div className="flex flex-col gap-2">
              {messages.map((message) => {
                const mine = message.sender_id === '__me__'
                const senderInitial = mine ? 'Me' : (message.sender_id || '?').charAt(0).toUpperCase()
                return (
                  <div key={message.message_id} className={`flex items-end gap-2 ${mine ? 'flex-row-reverse' : 'flex-row'}`}>
                    {!mine && (
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border/60 bg-muted text-[11px] font-semibold text-muted-foreground">
                        {senderInitial}
                      </span>
                    )}
                    <div className={`max-w-[78%] rounded-2xl px-4 py-2.5 text-sm ${
                      mine
                        ? 'bg-primary text-primary-foreground'
                        : 'border border-border/60 bg-background text-foreground'
                    }`}>
                      <p className="whitespace-pre-wrap leading-relaxed">{message.body}</p>
                      <p className={`mt-1 text-[10px] ${mine ? 'text-primary-foreground/60' : 'text-muted-foreground/60'}`}>
                        {message.created_at ? new Date(message.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <form onSubmit={sendMessage} className="border-t border-border/60 bg-background/50 p-3">
            <div className="flex items-end gap-2">
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(e) }
                }}
                rows={1}
                placeholder={`Message ${threadTitle}…`}
                className="min-h-10 flex-1 resize-none rounded-2xl border border-border bg-background px-4 py-2.5 text-sm text-foreground outline-none focus:border-primary"
              />
              <button
                type="submit"
                disabled={!body.trim() || sending}
                className="shrink-0 rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </form>
        </section>
      </div>
    </PageFrame>
  )
}
