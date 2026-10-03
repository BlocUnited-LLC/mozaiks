import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import * as shellActions from '../../chat-ui/src/navigation/shellActions.js';

const { filterPersonalAccountItems, isPersonalAccountItem, isSignInAvailable } = shellActions;
const require = createRequire(new URL('../package.json', import.meta.url));
const { transformSync } = require('esbuild');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// The /api/shell-config auth projections the shell receives (see build_app_auth_projection).
const authDisabled = { runtime: { enabled: false, provider: 'none', local_development: true, user: { id: 'anonymous' } } };
const demoMode = { runtime: { enabled: false, provider: 'none', local_development: false, user: null } };
const authEnabled = { runtime: { enabled: true, provider: 'jwt', local_development: false, user: null } };

// Studio's resolved profile menu: shell.json shortcuts plus the injected Admin Portal entry.
const studioProfileMenu = [
  { id: 'profile', label: 'Profile', action: 'navigate', path: '/me' },
  { id: 'admin-portal', label: 'Admin Portal', action: 'navigate', path: '/apps', requiresRole: 'admin' },
  { id: 'signout', label: 'Sign Out', action: 'signout' },
];
const studioMobileBar = [
  { id: 'create', label: 'Create', action: 'navigate', path: '/create?new=1' },
  { id: 'profile', label: 'Profile', action: 'navigate', path: '/me' },
];

test('only an explicit disabled runtime means nobody can sign in', () => {
  assert.equal(isSignInAvailable(authDisabled), false);
  assert.equal(isSignInAvailable(demoMode), false);
  assert.equal(isSignInAvailable(authEnabled), true);
  // A shell without the host projection (static configs) keeps its configured entries.
  assert.equal(isSignInAvailable(null), true);
  assert.equal(isSignInAvailable({}), true);
});

test('the /me route family and sign in/out are personal account entries', () => {
  for (const item of [
    { id: 'profile', path: '/me' },
    { id: 'account', href: '/me' },
    { id: 'support', path: '/me?tab=support-tickets' },
    { id: 'preferences', path: '/me/preferences' },
    { id: 'signout', action: 'signout' },
    { id: 'signin', action: 'signin' },
    { id: 'custom-exit', action: 'signout' },
  ]) {
    assert.equal(isPersonalAccountItem(item), true, JSON.stringify(item));
  }
  for (const item of [
    { id: 'admin-portal', path: '/apps' },
    { id: 'messages', path: '/messages' },
    { id: 'members', path: '/members' },
    { id: 'public-profile', path: '/u/someone' },
    null,
  ]) {
    assert.equal(isPersonalAccountItem(item), false, JSON.stringify(item));
  }
});

test('with auth disabled the Studio menu drops Profile and Sign Out but keeps Admin Portal', () => {
  assert.deepEqual(
    filterPersonalAccountItems(studioProfileMenu, authDisabled).map((item) => item.id),
    ['admin-portal'],
  );
  assert.deepEqual(
    filterPersonalAccountItems(studioMobileBar, authDisabled).map((item) => item.id),
    ['create'],
  );
  assert.deepEqual(filterPersonalAccountItems(studioProfileMenu, demoMode).map((item) => item.id), ['admin-portal']);
});

test('with auth enabled every configured entry is kept unchanged', () => {
  assert.equal(filterPersonalAccountItems(studioProfileMenu, authEnabled), studioProfileMenu);
  assert.equal(filterPersonalAccountItems(studioMobileBar, null), studioMobileBar);
  assert.deepEqual(filterPersonalAccountItems(undefined, authDisabled), []);
});

// The shell chrome components themselves, rendered with the real shellActions and a stubbed navigation context.
const localUser = { id: 'local-user', name: 'Local User', roles: ['admin', 'user'] };

function renderShellComponent(file, navigation, props = {}) {
  const source = readFileSync(new URL(`../../chat-ui/src/components/layout/${file}`, import.meta.url), 'utf8');
  const { code } = transformSync(source, { loader: 'jsx', format: 'cjs', jsx: 'automatic' });
  const modules = {
    'react-router-dom': { useLocation: () => ({ pathname: '/apps', search: '' }) },
    '../../styles/themeProvider': {
      DEFAULT_HEADER_CONFIG: { logo: { src: null, wordmark: null, alt: 'App', href: '/' }, actions: [] },
      DEFAULT_FOOTER_CONFIG: { links: [], visible: true },
    },
    '../../providers/NavigationProvider': { useNavigation: () => navigation },
    '../../navigation/useNavigationActions': { useNavigationActions: () => () => {} },
    '../../navigation/shellActions': shellActions,
    '../../context/ChatUIContext': { useChatUI: () => ({ user: localUser, login: () => {}, logout: () => {} }) },
    '../../ui/hooks/useAppEventBus.js': { useAppEventBus: () => {} },
    './notificationApi.js': { fetchNotificationCount: async () => null, clearNotifications: async () => null },
    './header-styles.css': {},
  };
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: (name) => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (Object.hasOwn(modules, name)) return modules[name];
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  return renderToStaticMarkup(React.createElement(module.exports.default, props));
}

const renderFooter = (navigation) => renderShellComponent('Footer.js', navigation);

const footerLabels = (html) => [...html.matchAll(/class="shell-footer-link"[^>]*>([^<]+)</g)].map((match) => match[1]);

// Footer links as /api/shell-config composes them from footer-scoped navigation items ({ label, href }).
const footerLinks = [
  { label: 'Account', href: '/me' },
  { label: 'Support', href: '/me?tab=support-tickets' },
  { label: 'Members', href: '/members' },
  { label: 'Privacy Policy', href: 'https://www.mozaiks.ai/privacy', external: true },
];

test('with auth disabled the footer renders no Account or Support link', () => {
  assert.deepEqual(
    footerLabels(renderFooter({ footer: { links: footerLinks }, auth: authDisabled })),
    ['Members', 'Privacy Policy'],
  );
  assert.deepEqual(
    footerLabels(renderFooter({ footer: { links: footerLinks }, auth: demoMode })),
    ['Members', 'Privacy Policy'],
  );
  // With only personal links configured nothing is left to show, so there is no footer.
  assert.equal(renderFooter({ footer: { links: footerLinks.slice(0, 2) }, auth: authDisabled }), '');
});

test('with auth enabled or no auth projection the footer renders every configured link', () => {
  const labels = footerLinks.map((link) => link.label);
  assert.deepEqual(footerLabels(renderFooter({ footer: { links: footerLinks }, auth: authEnabled })), labels);
  assert.deepEqual(footerLabels(renderFooter({ footer: { links: footerLinks }, auth: null })), labels);
});

// Header pills and header actions as an app might configure them; the mobile bar builds its auto items from both.
const chromeNavigation = (auth) => ({
  auth,
  headerPages: [
    { id: 'members', label: 'Members Directory', action: 'navigate', path: '/members' },
    { id: 'preferences', label: 'My Preferences Page', action: 'navigate', path: '/me/preferences' },
  ],
  header: {
    actions: [
      { id: 'signin', label: 'Sign In Now', action: 'signin' },
      { id: 'my-profile', label: 'Open My Profile', action: 'navigate', path: '/me' },
      { id: 'new-app', label: 'New App Draft', action: 'navigate', path: '/create?new=1' },
    ],
  },
  profile: { show: true, menu: [] },
  notifications: { show: false },
  mobile: { bottomBar: {} },
});
const shown = (html, labels) => labels.filter((label) => html.includes(label));
const chromeLabels = ['Members Directory', 'My Preferences Page', 'Sign In Now', 'Open My Profile', 'New App Draft'];

test('with auth disabled the header shows no personal pages or sign-in call to action', () => {
  const html = renderShellComponent('Header.js', chromeNavigation(authDisabled), { user: localUser });
  // The primary action falls through to the first entry the auth mode can honor.
  assert.deepEqual(shown(html, chromeLabels), ['Members Directory', 'New App Draft']);
});

test('with auth enabled the header shows every configured page and its first action', () => {
  const html = renderShellComponent('Header.js', chromeNavigation(authEnabled), { user: localUser });
  assert.deepEqual(shown(html, chromeLabels), ['Members Directory', 'My Preferences Page', 'Sign In Now']);
});

test('with auth disabled the mobile bar builds no personal items from header pages or actions', () => {
  assert.deepEqual(
    shown(renderShellComponent('MobileBottomBar.jsx', chromeNavigation(authDisabled)), chromeLabels),
    ['Members Directory', 'New App Draft'],
  );
  assert.deepEqual(
    shown(renderShellComponent('MobileBottomBar.jsx', chromeNavigation(authEnabled)), chromeLabels),
    ['Members Directory', 'My Preferences Page', 'Open My Profile'],
  );
});
