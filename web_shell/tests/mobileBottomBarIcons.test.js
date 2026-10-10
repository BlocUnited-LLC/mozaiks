import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const layout = path.join(root, 'chat-ui/src/components/layout');

const load = async (entry, plugins = []) => {
  const bundle = await build({
    entryPoints: [entry],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    packages: 'external',
    write: false,
    nodePaths: [path.join(root, 'web_shell/node_modules')],
    loader: { '.css': 'empty' },
    plugins,
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
};

const { default: ShellNavigationIcon, hasNamedIcon } = await load(path.join(layout, 'ShellNavigationIcon.jsx'));

// The bottom bar reads navigation and chat state from providers and routing
// from React Router. The stubs hand it plain objects so the server render
// exercises the real item derivation and icon wiring and nothing else.
const stubs = {
  NavigationProvider: 'export const useNavigation = () => globalThis.__mobileNavigation;',
  ChatUIContext: 'export const useChatUI = () => globalThis.__mobileChat;',
  notificationApi: 'export const fetchNotificationCount = async () => ({ unread_count: 0 });',
  useAppEventBus: 'export const useAppEventBus = () => {};',
  'react-router-dom': `export const useLocation = () => ({ pathname: '/apps', search: '' });
    export const useNavigate = () => () => {};`,
};
const { default: MobileBottomBar } = await load(path.join(layout, 'MobileBottomBar.jsx'), [{
  name: 'bottom-bar-boundaries',
  setup(builder) {
    builder.onResolve({ filter: /(?:NavigationProvider|ChatUIContext|notificationApi|useAppEventBus)(?:\.jsx?)?$|^react-router-dom$/ }, (args) => (
      { path: path.basename(args.path).replace(/\.jsx?$/, ''), namespace: 'bottom-bar-stub' }
    ));
    builder.onLoad({ filter: /.*/, namespace: 'bottom-bar-stub' }, (args) => (
      { contents: stubs[args.path], loader: 'js' }
    ));
  },
}]);

const markup = (icon, fallback = '?') => renderToStaticMarkup(
  React.createElement(ShellNavigationIcon, { icon, fallback }),
);

const renderBar = (navigation) => {
  globalThis.__mobileNavigation = {
    mobile: {},
    headerPages: [],
    header: { actions: [] },
    notifications: { show: false },
    profile: { show: false },
    ...navigation,
  };
  globalThis.__mobileChat = {
    user: { id: 'alice', roles: [] },
    isInWidgetMode: true,
    isWidgetVisible: true,
    isChatOverlayOpen: false,
    setIsChatOverlayOpen() {},
    unreadChatCount: 0,
    setUnreadChatCount() {},
    config: {},
  };
  return renderToStaticMarkup(React.createElement(MobileBottomBar));
};

// The glyph markup of the bottom-bar button that carries this label.
const glyph = (html, label) => {
  const button = html.split('<button').find((chunk) => chunk.includes(`shell-mobile-bottom-label">${label}</span>`));
  assert.ok(button, `no bottom-bar button labelled ${label} in ${html}`);
  return button.split('shell-mobile-bottom-glyph" aria-hidden="true">')[1].split('</span><span class="shell-mobile-bottom-label"')[0];
};

test('mobile navigation renders known manifest hints as icons', () => {
  for (const icon of ['store', 'analytics', 'apps', 'alerts', 'notifications']) {
    const html = markup(icon, 'X');
    assert.match(html, /<svg\b/);
    assert.doesNotMatch(html, />X</);
  }
});

test('mobile navigation renders asset icons and keeps an initial fallback', () => {
  assert.match(markup('custom.svg'), /mask:url\(\/assets\/custom\.svg\)/);
  assert.match(markup('/brand/custom.svg'), /mask:url\(\/brand\/custom\.svg\)/);
  assert.equal(markup('unknown-hint', 'R'), 'R');
  assert.equal(markup('constructor', 'C'), 'C');
  assert.equal(markup(null, 'D'), 'D');
});

test('only a named icon id can stand in for a missing icon hint', () => {
  assert.equal(hasNamedIcon('alerts'), true);
  assert.equal(hasNamedIcon('create-app'), true);
  for (const id of ['/docs', '/apps/demo', 'http://example.com', 'https://example.com/docs', 'constructor', 'custom.svg', 'unknown', '', null, undefined, 7]) {
    assert.equal(hasNamedIcon(id), false, `${id} must not be an icon hint`);
  }
});

test('bottom bar ids shaped like a path or href fall back to a letter, never a mask URL', () => {
  const html = renderBar({
    // A header page without an id takes its path as the id.
    headerPages: [{ label: 'Docs', path: '/docs' }],
    // A primary action without an id takes its href as the id.
    header: { actions: [{ label: 'Community', href: 'https://example.com/community' }] },
    notifications: { show: true, path: '/notifications' },
  });
  assert.doesNotMatch(html, /mask:url\(/);
  assert.equal(glyph(html, 'Docs'), 'D');
  assert.equal(glyph(html, 'Community'), 'C');
  assert.match(glyph(html, 'Alerts'), /<svg\b/);
});

test('configured bottom bar items use a named id as the icon hint and otherwise their initial', () => {
  const html = renderBar({
    mobile: { bottomBar: { items: [
      { id: 'alerts', label: 'Inbox', path: '/notifications' },
      { id: 'alerts', label: 'Special', path: '/special', iconLabel: 'S' },
      { id: '/docs', label: 'Docs', path: '/docs' },
      { id: 'reports', label: 'Reports', path: '/reports', icon: 'reports.svg' },
    ] } },
  });
  assert.match(glyph(html, 'Inbox'), /<svg\b/);
  assert.equal(glyph(html, 'Special'), 'S');
  assert.equal(glyph(html, 'Docs'), 'D');
  assert.match(glyph(html, 'Reports'), /mask:url\(\/assets\/reports\.svg\)/);
});
