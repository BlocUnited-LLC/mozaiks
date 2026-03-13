/**
 * Root application component.
 *
 * Mounts ChatUIProvider and the shared RootNavigator from @mozaiks/chat-ui/ui.
 * Platform bridge is already configured before this file is evaluated
 * (see index.js → src/platform/setup.ts).
 */

import React from 'react';
import { StatusBar, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ChatUIProvider } from '@mozaiks/chat-ui/core';
import { RootNavigator } from '@mozaiks/chat-ui/ui';
import { createMobileAuthAdapter } from './src/auth/createAuthAdapter';
import { getMobilePlatformConfig } from './src/platform/appConfig';

export default function App() {
  const mobileConfig = getMobilePlatformConfig();

  let authAdapter = null;
  let authError = null;

  try {
    authAdapter = createMobileAuthAdapter();
  } catch (error) {
    authError = error instanceof Error ? error.message : 'Failed to initialize mobile auth adapter.';
  }

  if (authError) {
    return (
      <SafeAreaProvider>
        <View style={styles.errorScreen}>
          <Text style={styles.errorTitle}>Mobile Auth Not Ready</Text>
          <Text style={styles.errorBody}>{authError}</Text>
          <Text style={styles.errorHint}>
            Current provider: {mobileConfig.auth.provider}
          </Text>
        </View>
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar barStyle="dark-content" />
      <ChatUIProvider authAdapter={authAdapter as any}>
        <RootNavigator />
      </ChatUIProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  errorScreen: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
    backgroundColor: '#f8fafc',
  },
  errorTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#111827',
    marginBottom: 12,
  },
  errorBody: {
    fontSize: 15,
    lineHeight: 22,
    color: '#374151',
    textAlign: 'center',
    marginBottom: 12,
  },
  errorHint: {
    fontSize: 13,
    color: '#6b7280',
  },
});
