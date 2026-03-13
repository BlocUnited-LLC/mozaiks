/**
 * ConversationListScreen — shared cross-platform conversation list.
 *
 * Fetches chat sessions from the REST API and lets the user start a new chat.
 * Works via react-native-web on browser, react-native on iOS/Android.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { platform } from '../../platform/index.js';

export type ChatSession = {
  id: string;
  title: string;
  last_message: string | null;
  updated_at: string;
};

type Props = {
  onSelectChat: (session: ChatSession) => void;
};

export default function ConversationListScreen({ onSelectChat }: Props) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { httpUrl } = platform.getBaseUrls();
      const token = platform.getAccessToken();
      const resp = await fetch(`${httpUrl}/api/chats`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: ChatSession[] = await resp.json();
      setSessions(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load chats');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const startNewChat = useCallback(async () => {
    try {
      const { httpUrl } = platform.getBaseUrls();
      const token = platform.getAccessToken();
      const resp = await fetch(`${httpUrl}/api/chats`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ title: 'New Chat' }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const session: ChatSession = await resp.json();
      onSelectChat(session);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create chat');
    }
  }, [onSelectChat]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#6366f1" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      <FlatList<ChatSession>
        data={sessions}
        keyExtractor={(item: ChatSession) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }: { item: ChatSession; index: number }) => (
          <Pressable
            style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
            onPress={() => onSelectChat(item)}
          >
            <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
            {item.last_message ? (
              <Text style={styles.rowPreview} numberOfLines={1}>{item.last_message}</Text>
            ) : null}
          </Pressable>
        )}
        ListEmptyComponent={
          <View style={styles.center}>
            <Text style={styles.emptyText}>No conversations yet</Text>
          </View>
        }
      />

      <Pressable
        style={({ pressed }) => [styles.newChatButton, pressed && styles.newChatButtonPressed]}
        onPress={startNewChat}
        accessibilityRole="button"
        accessibilityLabel="Start new chat"
      >
        <Text style={styles.newChatButtonText}>+ New Chat</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container:            { flex: 1, backgroundColor: '#f9fafb' },
  center:               { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list:                 { paddingVertical: 8 },
  row:                  { backgroundColor: '#fff', paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: '#e5e7eb' },
  rowPressed:           { backgroundColor: '#f3f4f6' },
  rowTitle:             { fontSize: 15, fontWeight: '600', color: '#111827' },
  rowPreview:           { fontSize: 13, color: '#6b7280', marginTop: 2 },
  emptyText:            { fontSize: 15, color: '#9ca3af' },
  errorBanner:          { backgroundColor: '#fee2e2', padding: 12, margin: 12, borderRadius: 8 },
  errorText:            { color: '#991b1b', fontSize: 13 },
  newChatButton:        { margin: 16, backgroundColor: '#6366f1', borderRadius: 10, paddingVertical: 14, alignItems: 'center' },
  newChatButtonPressed: { opacity: 0.8 },
  newChatButtonText:    { color: '#fff', fontSize: 16, fontWeight: '600' },
});
