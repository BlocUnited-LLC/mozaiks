import assert from 'node:assert/strict';
import test from 'node:test';
import {
  filterPersonalAccountItems,
  isPersonalAccountItem,
  isSignInAvailable,
} from '../../chat-ui/src/navigation/shellActions.js';

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
