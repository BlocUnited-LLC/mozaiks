import React, { useCallback, useEffect, useRef, useState } from "react";
import { moduleAction } from "../../../lib/moduleApi.js";

// Messages — custom_route_bundle for /messages
//
// Must use custom_route_bundle because:
//   1. Renders a conditional right-panel based on which thread is selected
//      (API response value at runtime)
//   2. Opens a real-time WebSocket connection for live message delivery
// (file_contracts.yaml hard constraint — cannot be declarative AppPageSchema)
//
// Actions: messages/list_threads, messages/get_thread, messages/send_message,
//          messages/mark_thread_read, messages/get_unread_summary,
//          messages/find_or_create_dm
// WebSocket: /api/modules/messages/ws/{user_id}
// Error branching: err.error_code / err.code (moduleApi.js preserves structured errors)
// No raw fetch() — all API calls go through moduleAction()

const PANEL = { NONE: "none", LOADING: "loading", READY: "ready", ERROR: "error" };

export default function Messages() {
  const [threads, setThreads] = useState([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState(null);
  const [nextThreadCursor, setNextThreadCursor] = useState(null);

  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [detailState, setDetailState] = useState(PANEL.NONE);

  const [composeText, setComposeText] = useState("");
  const [sending, setSending] = useState(false);

  const [unreadCount, setUnreadCount] = useState(0);
  const [filter, setFilter] = useState(null); // null = all, "dm", "group"

  const wsRef = useRef(null);
  // user_id is resolved from the platform session injected at bundle init time
  const userId = window.__MOZAIKS_USER__?.id ?? null;

  const loadThreads = useCallback(
    async (before = null) => {
      setThreadsLoading(true);
      setThreadsError(null);
      try {
        const res = await moduleAction("messages", "list_threads", {
          thread_type: filter,
          before,
          limit: 25,
        });
        const incoming = res.threads ?? [];
        setThreads((prev) => (before ? [...prev, ...incoming] : incoming));
        setNextThreadCursor(res.next_cursor ?? null);
      } catch (err) {
        const code = err.error_code ?? err.code ?? "UNKNOWN";
        setThreadsError(
          code === "PERMISSION_DENIED"
            ? "You do not have permission to view messages."
            : err.message ?? "Could not load conversations."
        );
      } finally {
        setThreadsLoading(false);
      }
    },
    [filter]
  );

  // Refresh unread summary independently of the thread list
  const refreshUnread = useCallback(async () => {
    try {
      const res = await moduleAction("messages", "get_unread_summary", {});
      setUnreadCount(res.unread_thread_count ?? 0);
    } catch (_) {}
  }, []);

  useEffect(() => {
    loadThreads();
    refreshUnread();
  }, [loadThreads, refreshUnread]);

  async function openThread(thread) {
    setSelectedThread(thread);
    setMessages([]);
    setDetailState(PANEL.LOADING);
    try {
      const res = await moduleAction("messages", "get_thread", {
        thread_id: thread.thread_id,
      });
      setMessages(res.messages ?? []);
      setDetailState(PANEL.READY);
      // Mark read — non-blocking
      moduleAction("messages", "mark_thread_read", {
        thread_id: thread.thread_id,
      })
        .then(() => {
          setThreads((prev) =>
            prev.map((t) =>
              t.thread_id === thread.thread_id ? { ...t, unread_count: 0 } : t
            )
          );
          refreshUnread();
        })
        .catch(() => {});
    } catch (err) {
      const code = err.error_code ?? err.code ?? "UNKNOWN";
      if (code === "THREAD_NOT_FOUND") {
        setDetailState(PANEL.ERROR);
      } else {
        setDetailState(PANEL.ERROR);
      }
    }
  }

  async function handleSend() {
    if (!composeText.trim() || !selectedThread) return;
    setSending(true);
    try {
      const res = await moduleAction("messages", "send_message", {
        thread_id: selectedThread.thread_id,
        body: composeText.trim(),
      });
      setMessages((prev) => [...prev, res.message ?? res]);
      setComposeText("");
    } catch (_) {
      // Non-blocking — compose field stays open so the user can retry
    } finally {
      setSending(false);
    }
  }

  // Real-time WebSocket for live message delivery
  useEffect(() => {
    if (!userId) return;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${protocol}://${window.location.host}/api/modules/messages/ws/${userId}`
    );
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      try {
        const event = JSON.parse(evt.data);

        if (event.event_type === "app.messages.message.sent") {
          const msg = event.payload;
          // Append to open thread if it matches
          setSelectedThread((current) => {
            if (current?.thread_id === msg.thread_id) {
              setMessages((prev) => [...prev, msg]);
            }
            return current;
          });
          // Bump thread to top and update unread badge
          setThreads((prev) => {
            const exists = prev.find((t) => t.thread_id === msg.thread_id);
            const updated = prev.map((t) => {
              if (t.thread_id !== msg.thread_id) return t;
              const isOpen =
                wsRef.current &&
                document.hasFocus() &&
                t.thread_id === msg.thread_id;
              return {
                ...t,
                last_message_at: msg.created_at,
                unread_count: isOpen ? 0 : (t.unread_count ?? 0) + 1,
              };
            });
            return exists ? updated : prev;
          });
          refreshUnread();
        }

        if (event.event_type === "notification.count_changed") {
          refreshUnread();
        }
      } catch (_) {}
    };

    ws.onclose = () => {
      wsRef.current = null;
    };

    return () => {
      ws.close();
    };
  }, [userId, refreshUnread]);

  return (
    <div
      className="messages-page"
      style={{ display: "flex", height: "100%", overflow: "hidden" }}
    >
      {/* Left panel — thread list */}
      <div
        className="messages-page__threads"
        style={{
          width: 300,
          borderRight: "1px solid var(--border-color, #e5e7eb)",
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        <div className="messages-page__threads-header">
          <h2 className="messages-page__threads-title">
            Messages
            {unreadCount > 0 && (
              <span className="messages-page__badge">{unreadCount}</span>
            )}
          </h2>
          <div className="messages-page__filters">
            {[
              { label: "All", value: null },
              { label: "DMs", value: "dm" },
              { label: "Groups", value: "group" },
            ].map((f) => (
              <button
                key={f.label}
                className={
                  "messages-page__filter-btn" +
                  (filter === f.value ? " messages-page__filter-btn--active" : "")
                }
                onClick={() => setFilter(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {threadsLoading && threads.length === 0 && (
          <p className="messages-page__state">Loading conversations&hellip;</p>
        )}
        {threadsError && (
          <p className="messages-page__state messages-page__state--error">
            {threadsError}
          </p>
        )}
        {!threadsLoading && !threadsError && threads.length === 0 && (
          <p className="messages-page__state">No conversations yet.</p>
        )}

        <ul className="messages-page__thread-list">
          {threads.map((t) => (
            <li
              key={t.thread_id}
              className={
                "messages-page__thread-item" +
                (selectedThread?.thread_id === t.thread_id
                  ? " messages-page__thread-item--active"
                  : "") +
                (t.unread_count > 0 ? " messages-page__thread-item--unread" : "")
              }
              onClick={() => openThread(t)}
            >
              <span className="messages-page__thread-name">
                {t.display_name ?? t.thread_id}
              </span>
              {t.unread_count > 0 && (
                <span className="messages-page__thread-badge">
                  {t.unread_count}
                </span>
              )}
            </li>
          ))}
        </ul>

        {nextThreadCursor && (
          <button
            className="messages-page__load-more"
            onClick={() => loadThreads(nextThreadCursor)}
            disabled={threadsLoading}
          >
            Load more
          </button>
        )}
      </div>

      {/* Right panel — message history + compose */}
      <div
        className="messages-page__detail"
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {detailState === PANEL.NONE && (
          <div className="messages-page__empty">
            <p>Select a conversation or start a new one.</p>
          </div>
        )}

        {detailState === PANEL.LOADING && (
          <p className="messages-page__state">Loading messages&hellip;</p>
        )}

        {detailState === PANEL.ERROR && (
          <p className="messages-page__state messages-page__state--error">
            Could not load messages. Please try again.
          </p>
        )}

        {detailState === PANEL.READY && (
          <>
            <div
              className="messages-page__messages"
              style={{ flex: 1, overflowY: "auto", padding: "1rem" }}
            >
              {messages.length === 0 && (
                <p className="messages-page__state">
                  No messages yet. Say hello!
                </p>
              )}
              {messages.map((m) => (
                <div
                  key={m.message_id}
                  className="messages-page__message"
                >
                  <span className="messages-page__message-sender">
                    {m.sender_id}
                  </span>
                  <span className="messages-page__message-body">{m.body}</span>
                </div>
              ))}
            </div>

            <div className="messages-page__compose">
              <textarea
                className="messages-page__compose-input"
                value={composeText}
                rows={2}
                placeholder="Type a message&hellip;"
                onChange={(e) => setComposeText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <button
                className="messages-page__compose-send"
                onClick={handleSend}
                disabled={sending || !composeText.trim()}
              >
                {sending ? "Sending\u2026" : "Send"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
