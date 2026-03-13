/**
 * @format
 * React Native entry point.
 *
 * Platform bridge is configured in src/platform/setup before any screen is
 * mounted, ensuring useCoreWebSocket, ChatUIProvider, and the storage layer
 * are all wired before the first render.
 */

import './src/platform/setup';
import { AppRegistry } from 'react-native';
import App from './App';
import { name as appName } from './app.json';

AppRegistry.registerComponent(appName, () => App);
