/**
 * @mozaiks/chat-ui/ui — cross-platform UI layer
 *
 * Components and screens built on React Native primitives.
 * On web: react-native-web translates them to DOM.
 * On native: react-native renders them natively.
 *
 * Import from here in both app/ (web) and clients/mobile/ (native).
 */

export { default as MessageBubble } from './components/MessageBubble.js';
export { default as MessageInput } from './components/MessageInput.js';
export { default as ConversationListScreen } from './screens/ConversationListScreen.js';
export { default as ChatScreen } from './screens/ChatScreen.js';
export { default as RootNavigator } from './navigation/index.js';
