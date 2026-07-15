import React, { useCallback, useEffect, useMemo, useState } from 'react'

async function moduleAction(moduleId, actionId, payload = {}, appId = null) {
  const query = appId ? `?app_id=${encodeURIComponent(appId)}` : ''
  const response = await fetch(`/api/modules/${moduleId}/${actionId}${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`${moduleId}.${actionId} failed with ${response.status}`)
  }
  return response.json()
}

function resolveAppId() {
  if (typeof window === 'undefined') return 'default'
  const params = new URLSearchParams(window.location.search)
  return params.get('app_id') || window.__MOZAIKS_APP_ID__ || 'default'
}

function titleForRequest(request) {
  return request.subject || request.page_title || String(request.message || 'Support request').slice(0, 80)
}

export default function Support() {
  const appId = useMemo(() => resolveAppId(), [])
  const [requests, setRequests] = useState([])
  const [activeRequestId, setActiveRequestId] = useState(null)
  const [activeThread, setActiveThread] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [newMessage, setNewMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const activeRequest = requests.find((request) => request.request_id === activeRequestId) || null

  const loadRequests = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await moduleAction('support', 'list_support_requests', {
        status: 'all',
        scope: 'user',
        app_id: appId,
        limit: 50,
      }, appId)
      const items = Array.isArray(result.requests) ? result.requests : []
      setRequests(items)
      if (!activeRequestId && items.length > 0) setActiveRequestId(items[0].request_id)
    } catch (err) {
      setError(err.message || 'Support could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [activeRequestId, appId])

  const loadThread = useCallback(async (request) => {
    if (!request?.message_thread_id) {
      setActiveThread(null)
      setMessages([])
      return
    }
    const result = await moduleAction('messages', 'get_thread', {
      thread_id: request.message_thread_id,
      message_limit: 100,
    }, appId)
    setActiveThread(result.thread || null)
    setMessages(Array.isArray(result.messages) ? result.messages : [])
  }, [appId])

  useEffect(() => {
    loadRequests()
  }, [loadRequests])

  useEffect(() => {
    loadThread(activeRequest)
  }, [activeRequest, loadThread])

  async function handleCreate(event) {
    event.preventDefault()
    const body = draft.trim()
    if (!body) return
    const created = await moduleAction('support', 'create_support_request', {
      message: body,
      subject: body.slice(0, 80),
      severity: 'low',
      app_id: appId,
    }, appId)
    const request = created.request
    const threadResult = await moduleAction('messages', 'create_thread', {
      title: titleForRequest(request),
      participant_ids: [],
      thread_type: 'support',
      scope_type: 'app',
      scope_id: appId,
      subject_app_id: appId,
      related_type: 'support.request',
      related_id: request.request_id,
      metadata: { request_id: request.request_id },
    }, appId)
    const threadId = threadResult.thread?.thread_id
    if (threadId) {
      await moduleAction('support', 'link_message_thread', {
        request_id: request.request_id,
        message_thread_id: threadId,
      }, appId)
      await moduleAction('messages', 'send_message', {
        thread_id: threadId,
        body,
      }, appId)
    }
    setDraft('')
    await loadRequests()
    setActiveRequestId(request.request_id)
  }

  async function handleReply(event) {
    event.preventDefault()
    const body = newMessage.trim()
    if (!body || !activeRequest?.message_thread_id) return
    await moduleAction('messages', 'send_message', {
      thread_id: activeRequest.message_thread_id,
      body,
    }, appId)
    setNewMessage('')
    await loadThread(activeRequest)
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-120px)] w-full max-w-6xl flex-col gap-6 px-4 py-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-normal text-foreground">Support</h1>
        <p className="mt-2 text-sm text-muted-foreground">Create a request and continue the conversation with support.</p>
      </header>

      <form onSubmit={handleCreate} className="rounded-lg border border-border bg-card p-4">
        <label className="text-sm font-medium text-foreground" htmlFor="support-request">New request</label>
        <div className="mt-3 flex gap-3">
          <textarea
            id="support-request"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="min-h-20 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
            placeholder="What do you need help with?"
          />
          <button className="h-10 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground" type="submit">
            Send
          </button>
        </div>
      </form>

      {error && <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <section className="grid min-h-[480px] gap-4 lg:grid-cols-[320px_1fr]">
        <aside className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-4 py-3 text-sm font-medium text-foreground">Requests</div>
          {loading ? (
            <div className="p-4 text-sm text-muted-foreground">Loading...</div>
          ) : requests.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">No support requests yet.</div>
          ) : (
            <div className="divide-y divide-border">
              {requests.map((request) => (
                <button
                  key={request.request_id}
                  type="button"
                  onClick={() => setActiveRequestId(request.request_id)}
                  className={`block w-full px-4 py-3 text-left text-sm ${activeRequestId === request.request_id ? 'bg-primary/10' : 'hover:bg-muted/40'}`}
                >
                  <div className="font-medium text-foreground">{titleForRequest(request)}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{request.status || 'open'} - {request.severity || 'low'}</div>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section className="flex min-h-0 flex-col rounded-lg border border-border bg-card">
          <div className="border-b border-border px-4 py-3">
            <div className="text-sm font-medium text-foreground">{activeRequest ? titleForRequest(activeRequest) : 'Select a request'}</div>
            {activeRequest && <div className="mt-1 text-xs text-muted-foreground">{activeRequest.request_id}</div>}
          </div>
          <div className="flex-1 space-y-3 overflow-auto p-4">
            {!activeRequest ? (
              <p className="text-sm text-muted-foreground">Choose a request to view the conversation.</p>
            ) : messages.length === 0 ? (
              <p className="text-sm text-muted-foreground">No messages are linked to this request yet.</p>
            ) : (
              messages.map((message) => (
                <article key={message.message_id} className="rounded-lg border border-border bg-background p-3">
                  <div className="text-xs text-muted-foreground">{message.sender_role || 'user'}</div>
                  <p className="mt-1 text-sm text-foreground">{message.body}</p>
                </article>
              ))
            )}
          </div>
          {activeThread && (
            <form onSubmit={handleReply} className="flex gap-3 border-t border-border p-4">
              <input
                value={newMessage}
                onChange={(event) => setNewMessage(event.target.value)}
                className="h-10 flex-1 rounded-lg border border-border bg-background px-3 text-sm text-foreground"
                placeholder="Reply..."
              />
              <button className="rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground" type="submit">
                Reply
              </button>
            </form>
          )}
        </section>
      </section>
    </main>
  )
}
