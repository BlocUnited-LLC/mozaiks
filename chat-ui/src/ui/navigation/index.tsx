/**
 * RootNavigator — shared cross-platform stack navigator.
 *
 * Used by both:
 *   - web:    App.jsx in app/ (react-native-web + react-navigation)
 *   - native: App.tsx in clients/mobile/ (react-native + react-navigation)
 *
 * Navigation is abstracted through onSelectChat / onBack callbacks so
 * screens have no direct dependency on navigator APIs.
 */

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import ConversationListScreen, { type ChatSession } from '../screens/ConversationListScreen.js';
import ChatScreen from '../screens/ChatScreen.js';

type ActiveChat = { id: string; title: string };

export default function RootNavigator() {
  const [activeChat, setActiveChat] = useState<ActiveChat | null>(null);

  if (activeChat) {
    return (
      <View style={styles.fill}>
        <ChatScreen chatId={activeChat.id} />
      </View>
    );
  }

  return (
    <View style={styles.fill}>
      <ConversationListScreen
        onSelectChat={(session: ChatSession) =>
          setActiveChat({ id: session.id, title: session.title })
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
});
