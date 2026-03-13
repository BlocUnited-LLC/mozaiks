/**
 * MessageBubble — shared cross-platform chat bubble.
 *
 * Uses React Native primitives so it renders correctly via:
 *   - react-native-web  on the browser (Vite build)
 *   - react-native      on iOS / Android (Metro build)
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

export type Message = {
  id: string;
  sender: 'user' | 'agent';
  content: string;
  agentName?: string;
  isStreaming?: boolean;
};

type Props = {
  message: Message;
};

export default function MessageBubble({ message }: Props) {
  const isUser = message.sender === 'user';

  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowAgent]}>
      {!isUser && message.agentName ? (
        <Text style={styles.agentName}>{message.agentName}</Text>
      ) : null}
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAgent]}>
        <Text style={[styles.text, isUser && styles.textUser]}>
          {message.content}
          {message.isStreaming ? <Text style={styles.cursor}>▌</Text> : null}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row:         { marginVertical: 4, maxWidth: '82%' },
  rowUser:     { alignSelf: 'flex-end',  alignItems: 'flex-end' },
  rowAgent:    { alignSelf: 'flex-start', alignItems: 'flex-start' },
  agentName:   { fontSize: 11, color: '#9ca3af', marginBottom: 2, marginLeft: 4 },
  bubble:      { borderRadius: 16, paddingHorizontal: 14, paddingVertical: 9 },
  bubbleUser:  { backgroundColor: '#6366f1' },
  bubbleAgent: { backgroundColor: '#ffffff', borderWidth: StyleSheet.hairlineWidth, borderColor: '#e5e7eb' },
  text:        { fontSize: 15, color: '#111827', lineHeight: 22 },
  textUser:    { color: '#ffffff' },
  cursor:      { color: '#9ca3af' },
});
