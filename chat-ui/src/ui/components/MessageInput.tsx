/**
 * MessageInput — shared cross-platform text input row.
 *
 * Renders via react-native-web on browser, react-native on iOS/Android.
 */

import React, { useCallback, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  TextInput,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

type Props = {
  onSend: (text: string) => void;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
};

export default function MessageInput({ onSend, disabled = false, style }: Props) {
  const [text, setText] = useState('');

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
  }, [text, disabled, onSend]);

  return (
    <View style={[styles.container, style]}>
      <TextInput
        style={styles.input}
        value={text}
        onChangeText={setText}
        placeholder="Message…"
        placeholderTextColor="#9ca3af"
        multiline
        maxLength={4000}
        editable={!disabled}
        accessibilityLabel="Message input"
      />
      <Pressable
        style={({ pressed }) => [
          styles.sendButton,
          (!text.trim() || disabled) && styles.sendButtonDisabled,
          pressed && styles.sendButtonPressed,
        ]}
        onPress={handleSend}
        disabled={!text.trim() || disabled}
        accessibilityRole="button"
        accessibilityLabel="Send message"
      >
        <View style={styles.arrowIcon} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container:            { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: 12, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: '#e5e7eb', backgroundColor: '#fff' },
  input:                { flex: 1, minHeight: 40, maxHeight: 120, fontSize: 15, color: '#111827', backgroundColor: '#f3f4f6', borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, marginRight: 8 },
  sendButton:           { width: 40, height: 40, borderRadius: 20, backgroundColor: '#6366f1', alignItems: 'center', justifyContent: 'center' },
  sendButtonDisabled:   { backgroundColor: '#c7d2fe' },
  sendButtonPressed:    { opacity: 0.8 },
  arrowIcon:            { width: 0, height: 0, borderLeftWidth: 7, borderRightWidth: 7, borderBottomWidth: 12, borderLeftColor: 'transparent', borderRightColor: 'transparent', borderBottomColor: '#fff' },
});
