/**
 * Metro bundler configuration.
 *
 * Extends the default config with two additions:
 *
 * 1. watchFolders — includes the monorepo root so Metro watches changes to
 *    @mozaiks/chat-ui source files during development.
 *
 * 2. resolver.nodeModulesPaths — adds the root node_modules so that shared
 *    React/React Native peer deps resolve from one location, avoiding the
 *    "multiple React instances" problem in a monorepo.
 */

const { getDefaultConfig, mergeConfig } = require('@react-native/metro-config');
const path = require('path');

const monorepoRoot = path.resolve(__dirname, '../..');

const config = {
  watchFolders: [monorepoRoot],
  resolver: {
    // Ensure React resolves from the mobile package's node_modules to avoid
    // duplicate React instances when chat-ui's node_modules also has React.
    nodeModulesPaths: [
      path.resolve(__dirname, 'node_modules'),
      path.resolve(monorepoRoot, 'node_modules'),
    ],
  },
};

module.exports = mergeConfig(getDefaultConfig(__dirname), config);
