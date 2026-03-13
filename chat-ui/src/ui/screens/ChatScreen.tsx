/**
 * ChatScreen — shared cross-platform active conversation view.
 *
 * Uses shared hooks from core (useChatUI) and platform bridge for
 * backend URLs. Renders via react-native-web on browser, react-native
 * on iOS/Android.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  View,
} from 'react-native';
import { useChatUI } from '../../context/ChatUIContext.jsx';
import { platform } from '../../platform/index.js';
import MessageBubble, { type Message } from '../components/MessageBubble.js';
import MessageInput from '../components/MessageInput.js';

type Props = {
  chatId: string;
};

type ChatUIContextValue = {
  setActiveChatId: (chatId: string | null) => void;
};

export default function ChatScreen({ chatId }: Props) {
  const { setActiveChatId } = useChatUI() as ChatUIContextValue;
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const listRef = useRef<FlatList<Message>>(null);

  useEffect(() => {
    setActiveChatId(chatId);
    return () => setActiveChatId(null);
  }, [chatId, setActiveChatId]);

  // Fetch message history
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { httpUrl } = platform.getBaseUrls();
        const token = platform.getAccessToken();
        const resp = await fetch(`${httpUrl}/api/chats/${encodeURIComponent(chatId)}/messages`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled) {
          setMessages(
            (data as Array<{ id: string; role: string; content: string; agent?: string }>).map(
              (m) => ({
                id: m.id,
                sender: m.role === 'assistant' ? 'agent' : 'user',
                agentName: m.agent ?? undefined,
                content: m.content,
              }),
            ),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [chatId]);

  // Live WebSocket token streaming
  useEffect(() => {
    const { wsUrl } = platform.getBaseUrls();
    const ws = new WebSocket(`${wsUrl}/ws/chat/${encodeURIComponent(chatId)}`);

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data as string) as {
          type: string;
          content?: string;
          agent?: string;
          id?: string;
        };
        if (payload.type === 'token') {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.sender === 'agent' && last?.isStreaming) {
              return [...prev.slice(0, -1), { ...last, content: last.content + (payload.content ?? '') }];
            }
            return [...prev, { id: payload.id ?? `stream-${Date.now()}`, sender: 'agent', agentName: payload.agent, content: payload.content ?? '', isStreaming: true }];
          });
        } else if (payload.type === 'message_done') {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            return last?.isStreaming ? [...prev.slice(0, -1), { ...last, isStreaming: false }] : prev;
          });
        }
      } catch { /* ignore malformed frames */ }
    };

    return () => { ws.close(); };
  }, [chatId]);

  useEffect(() => {
    if (messages.length > 0) listRef.current?.scrollToEnd({ animated: true });
  }, [messages]);

  const handleSend = useCallback(async (text: string) => {
    if (!text.trim() || sending) return;
    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, sender: 'user', content: text.trim() }]);
    setSending(true);
    try {
      const { httpUrl } = platform.getBaseUrls();
      const token = platform.getAccessToken();
      await fetch(`${httpUrl}/api/chats/${encodeURIComponent(chatId)}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ content: text.trim(), role: 'user' }),
      });
    } finally {
      setSending(false);
    }
  }, [chatId, sending]);

  if (loading) {
    return <View style={styles.center}><ActivityIndicator size="large" color="#6366f1" /></View>;
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={90}
    >
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.messageList}
        renderItem={({ item }) => <MessageBubble message={item} />}
      />
      <MessageInput onSend={handleSend} disabled={sending} />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container:   { flex: 1, backgroundColor: '#f9fafb' },
  center:      { flex: 1, alignItems: 'center', justifyContent: 'center' },
  messageList: { paddingHorizontal: 12, paddingTop: 12, paddingBottom: 8 },
});
