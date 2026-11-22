import React, { useCallback, useEffect, useRef, useState } from 'react'
import { moduleAction } from '../lib/moduleApi.js'

function titleForRequest(request) {
  return request.subject || request.page_title || String(request.message || 'Support request').slice(0, 80)
}

export default function SupportProfileTab({ operator = false }) {
  const [requests, setRequests] = useState([])
  const [activeRequestId, setActiveRequestId] = useState(() => typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('request_id'))
  const [activeThread, setActiveThread] = useState(null)
  const [messages, setMessages] = useState([])
  const [newMessage, setNewMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [replying, setReplying] = useState(false)
  const [threadLoading, setThreadLoading] = useState(false)
  const replyPending = useRef(false)
  const mounted = useRef(true)
  const selectedRequest = useRef(activeRequestId)
  selectedRequest.current = activeRequestId
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const activeRequest = requests.find((request) => request.request_id === activeRequestId) || null

  const loadRequests = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await moduleAction('support', 'list_support_requests', {
        status: 'all',
        scope: operator ? 'app' : 'user',
        limit: 50,
      })
      if (!mounted.current) return
      const items = Array.isArray(result.requests) ? result.requests : []
      setRequests(items)
      if (!activeRequestId && items.length > 0) setActiveRequestId(items[0].request_id)
    } catch (err) {
      if (mounted.current) setError(err.message || 'Support could not be loaded.')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [activeRequestId, operator])

  const loadThread = useCallback(async (request) => {
    setActiveThread(null)
    setMessages([])
    if (!request?.message_thread_id) {
      setThreadLoading(false)
      return
    }
    setThreadLoading(true)
    try {
      const result = await moduleAction('support', 'get_support_conversation', {
        request_id: request.request_id,
      })
      if (!mounted.current || selectedRequest.current !== request.request_id) return
      if (!result.success) throw new Error(result.error || 'Support conversation could not be loaded.')
      setActiveThread(result.thread || null)
      setMessages(Array.isArray(result.messages) ? result.messages : [])
    } catch (err) {
      if (!mounted.current || selectedRequest.current !== request.request_id) return
      setActiveThread(null)
      setMessages([])
      setError(err.message || 'Support conversation could not be loaded.')
    } finally {
      if (mounted.current && selectedRequest.current === request.request_id) setThreadLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRequests()
  }, [loadRequests])

  useEffect(() => {
    loadThread(activeRequest)
  }, [activeRequest, loadThread])

  async function handleReply(event) {
    event.preventDefault()
    const body = newMessage.trim()
    if (!body || !activeRequest?.message_thread_id || replyPending.current || threadLoading
        || activeThread?.thread_id !== activeRequest.message_thread_id) return
    replyPending.current = true
    setReplying(true)
    try {
      setError(null)
      const sent = await moduleAction('support', 'reply_support_request', {
        request_id: activeRequest.request_id,
        body,
      })
      if (!mounted.current) return
      if (!sent.success) throw new Error(sent.error || 'Support reply could not be sent.')
      setNewMessage('')
      await loadThread(activeRequest)
    } catch (err) {
      if (mounted.current) setError(err.message || 'Support reply could not be sent.')
    } finally {
      replyPending.current = false
      if (mounted.current) setReplying(false)
    }
  }

  return (
    <section aria-label={operator ? "Support inbox" : "Your support requests"} className="flex w-full flex-col gap-4">
      {error && <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

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
                  disabled={replying}
                  onClick={() => {
                    if (request.request_id === activeRequestId) return
                    setActiveThread(null)
                    setMessages([])
                    setNewMessage('')
                    setThreadLoading(true)
                    setActiveRequestId(request.request_id)
                  }}
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
            {threadLoading ? <p>Loading conversation...</p> : !activeRequest ? (
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
          {activeThread && !threadLoading && activeThread.thread_id === activeRequest?.message_thread_id && (
            <form onSubmit={handleReply} className="flex gap-3 border-t border-border p-4">
              <input
                aria-label="Reply to support request"
                disabled={replying}
                value={newMessage}
                onChange={(event) => setNewMessage(event.target.value)}
                className="h-10 flex-1 rounded-lg border border-border bg-background px-3 text-sm text-foreground"
                placeholder="Reply..."
              />
              <button disabled={replying || !newMessage.trim()} className="rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground" type="submit">
                Reply
              </button>
            </form>
          )}
        </section>
      </section>
    </section>
  )
}

export function SupportAdminPanel() {
  return <SupportProfileTab operator />
}
