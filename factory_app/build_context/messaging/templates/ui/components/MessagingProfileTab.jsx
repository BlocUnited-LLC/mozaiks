import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, authHeaders, moduleAction } from "../lib/moduleApi.js";

function relativeTime(value) {
  if (!value) return "";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function threadTitle(thread) {
  return thread?.title || thread?.related_type || thread?.thread_type || "Conversation";
}

function messageTime(value) {
  const timestamp = new Date(value || "").getTime();
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : "";
}

export default function MessagingProfileTab({ data }) {
  const [threads, setThreads] = useState(Array.isArray(data?.threads) ? data.threads : []);
  const [creating, setCreating] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [recipients, setRecipients] = useState("");
  const createPending = useRef(false);
  const mounted = useRef(true);
  const sendPending = useRef(false);
  const [selectedId, setSelectedId] = useState(threads[0]?.thread_id || null);
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [query, setQuery] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => { setThreads(Array.isArray(data?.threads) ? data.threads : []); }, [data]);

  async function refreshConversations() {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      const result = await moduleAction('messages', 'list_threads', {});
      if (!mounted.current) return;
      const updated = Array.isArray(result?.threads) ? result.threads : [];
      setThreads(updated);
      if (!selectedIdRef.current) setSelectedId(updated[0]?.thread_id || null);
    } catch (failure) {
      if (mounted.current) setError(failure.message || 'Could not refresh conversations.');
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }

  async function createConversation(event) {
    event.preventDefault();
    const usernames = [...new Set(recipients.split(',').map(value => value.trim()).filter(Boolean))];
    if (!usernames.length || createPending.current) return;
    createPending.current = true;
    setCreating(true);
    setError(null);
    try {
      const participantIds = await Promise.all(usernames.map(async username => {
        const response = await fetch(`${API_BASE}/api/users/${encodeURIComponent(username)}`, { headers: authHeaders() });
        if (!response.ok) throw new Error(`Could not find user ${username}.`);
        const profile = await response.json();
        if (!profile.user_id) throw new Error(`Could not find user ${username}.`);
        return profile.user_id;
      }));
      if (!mounted.current) return;
      const result = await moduleAction('messages', 'create_thread', {
        participant_ids: [...new Set(participantIds)],
        thread_type: participantIds.length === 1 ? 'direct' : 'group',
      });
      if (!mounted.current) return;
      if (!result?.success || !result.thread?.thread_id) throw new Error(result?.error || 'Could not create conversation.');
      setThreads(current => [result.thread, ...current.filter(thread => thread.thread_id !== result.thread.thread_id)]);
      setSelectedId(result.thread.thread_id);
      setRecipients('');
      setShowCreate(false);
    } catch (failure) {
      if (mounted.current) setError(failure.message || 'Could not create conversation.');
    } finally {
      createPending.current = false;
      if (mounted.current) setCreating(false);
    }
  }

  const visibleThreads = useMemo(() => {
    const value = query.trim().toLowerCase();
    return value ? threads.filter((thread) => threadTitle(thread).toLowerCase().includes(value)) : threads;
  }, [query, threads]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedThread(null);
      setMessages([]);
      return undefined;
    }
    let active = true;
    setSelectedThread(null);
    setMessages([]);
    setLoading(true);
    setError(null);
    moduleAction("messages", "get_thread", { thread_id: selectedId, message_limit: 100 })
      .then((result) => {
        if (!active) return;
        setSelectedThread(result?.thread || threads.find((thread) => thread.thread_id === selectedId) || null);
        setMessages(Array.isArray(result?.messages) ? result.messages : []);
        if (result?.thread?.thread_id) moduleAction("messages", "mark_thread_read", { thread_id: selectedId }).catch(() => {});
      })
      .catch(() => active && setError("This conversation is unavailable."))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [selectedId, threads]);

  async function sendMessage(event) {
    event.preventDefault();
    const value = body.trim();
    if (!value || !selectedId || sendPending.current) return;
    const threadId = selectedId;
    sendPending.current = true;
    setSending(true);
    setError(null);
    try {
      const sent = await moduleAction("messages", "send_message", { thread_id: selectedId, body: value });
      if (!sent?.success) throw new Error(sent?.error || "Could not send message.");
      if (!mounted.current || selectedIdRef.current !== threadId) return;
      setBody("");
      const result = await moduleAction("messages", "get_thread", { thread_id: selectedId, message_limit: 100 });
      if (!mounted.current || selectedIdRef.current !== threadId) return;
      setMessages(Array.isArray(result?.messages) ? result.messages : []);
      setSelectedThread(result?.thread || selectedThread);
    } catch (_error) {
      if (mounted.current && selectedIdRef.current === threadId) setError("Could not send message.");
    } finally {
      sendPending.current = false;
      if (mounted.current) setSending(false);
    }
  }

  return (
    <section className="grid grid-cols-1 md:grid-cols-[minmax(0,30%)_minmax(0,1fr)]" style={{ border: "1px solid var(--color-border)", minHeight: 420, overflow: "hidden" }}>
      <aside style={{ minWidth: 0, borderRight: "1px solid var(--color-border)" }}>
        <header style={{ borderBottom: "1px solid var(--color-border)", padding: 12 }}>
          <strong>Messages</strong>
          <button type="button" onClick={refreshConversations} disabled={refreshing}>Refresh</button>
          <button type="button" onClick={() => setShowCreate(current => !current)}>New conversation</button>
          {showCreate && <form onSubmit={createConversation}>
            <label>Usernames<input value={recipients} onChange={event => setRecipients(event.target.value)} placeholder="Usernames, comma separated" disabled={creating} /></label>
            <button type="submit" disabled={creating || !recipients.trim()}>{creating ? 'Creating…' : 'Start conversation'}</button>
          </form>}
          {error && <p role="alert">{error}</p>}
          <input aria-label="Search conversations" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search conversations" style={{ display: "block", marginTop: 10, maxWidth: "100%", width: "100%" }} />
        </header>
        <div>
          {visibleThreads.map((thread) => <button key={thread.thread_id} type="button" onClick={() => setSelectedId(thread.thread_id)} style={{ background: selectedId === thread.thread_id ? "var(--color-surface-alt)" : "transparent", border: 0, borderBottom: "1px solid var(--color-border)", color: "var(--color-text-primary)", cursor: "pointer", display: "block", padding: 12, textAlign: "left", width: "100%" }}><strong>{threadTitle(thread)}</strong><span style={{ display: "block", fontSize: 12, color: "var(--color-text-secondary)" }}>{thread.last_message?.body_preview || "No messages yet"}</span><span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{relativeTime(thread.updated_at)}</span></button>)}
          {visibleThreads.length === 0 && <p style={{ margin: 0, padding: 12, color: "var(--color-text-secondary)" }}>{query ? "No conversations match your search." : "No conversations yet."}</p>}
        </div>
      </aside>
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        {selectedThread ? <>
          <header style={{ borderBottom: "1px solid var(--color-border)", padding: 12 }}><strong>{threadTitle(selectedThread)}</strong></header>
          <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>{loading ? <p>Loading conversation...</p> : messages.length ? messages.map((message) => <div key={message.message_id} style={{ marginBottom: 10 }}><p style={{ margin: 0 }}>{message.body}</p><span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{messageTime(message.created_at)}</span></div>) : <p>No messages yet. Say something.</p>}</div>
          <form onSubmit={sendMessage} style={{ borderTop: "1px solid var(--color-border)", display: "flex", gap: 8, padding: 12 }}><input aria-label="Message conversation" value={body} onChange={(event) => setBody(event.target.value)} placeholder="Message conversation..." style={{ flex: 1, minWidth: 0 }} /><button type="submit" disabled={!body.trim() || sending}>{sending ? "Sending..." : "Send"}</button></form>
        </> : <div style={{ alignItems: "center", display: "flex", flex: 1, justifyContent: "center", padding: 24, textAlign: "center" }}><div><strong>Your conversation space</strong><p style={{ color: "var(--color-text-secondary)" }}>Select a conversation to start messaging.</p></div></div>}
      </div>
    </section>
  );
}
