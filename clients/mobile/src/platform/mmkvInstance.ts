/**
 * MMKV singleton.
 *
 * react-native-mmkv provides a synchronous key-value store backed by native
 * code. The platform bridge requires synchronous reads (used in useReducer
 * initialisers), so AsyncStorage is not an option here.
 */

import { MMKV } from 'react-native-mmkv';

export const storage = new MMKV({ id: 'mozaiks-chat-ui' });
