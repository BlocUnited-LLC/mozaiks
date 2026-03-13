/**
 * Babel configuration for the React Native mobile client.
 *
 * Uses metro-react-native-babel-preset as the foundation and adds
 * babel-plugin-module-resolver to resolve @mozaiks/chat-ui subpath
 * imports to the monorepo source directory.
 *
 * This is required because Metro's package exports support is still
 * experimental; explicit aliases are more reliable.
 */

module.exports = {
  presets: ['module:@react-native/babel-preset'],
  plugins: [
    [
      'module-resolver',
      {
        root: ['./'],
        alias: {
          // Resolve @mozaiks/chat-ui and every subpath to the workspace src.
          '@mozaiks/chat-ui':                    '../../chat-ui/src/index.js',
          '@mozaiks/chat-ui/core':               '../../chat-ui/src/core/index.js',
          '@mozaiks/chat-ui/platform':           '../../chat-ui/src/platform/index.js',
          '@mozaiks/chat-ui/ui':                 '../../chat-ui/src/ui/index.js',
          '@mozaiks/chat-ui/coreBridge':         '../../chat-ui/src/coreBridge.js',
          '@mozaiks/chat-ui/adminPortalRegistry':'../../chat-ui/src/adminPortalRegistry.js',
        },
      },
    ],
  ],
};
