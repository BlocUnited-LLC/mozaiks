import { createNativeAuthorizationTransport } from './authorizationTransport.mjs';

export async function createNativeAppAuthAdapter(options, { App, Browser, createSharedAuthAdapter, platform, window }) {
  if (platform !== 'android') throw new Error('This mobile reference requires Android');
  const auth = options.authConfig;
  if (auth?.required && auth.runtime?.enabled) {
    const native = auth.frontend?.android;
    const expected = options.env?.VITE_OIDC_REDIRECT_URI;
    if (!native || typeof native.client_id !== 'string' || !native.client_id.trim()
        || native.client_id === auth.frontend.client_id
        || typeof native.redirect_uri !== 'string' || !native.redirect_uri.trim()) {
      throw new Error('The backend must configure a separately registered Android sign-in client');
    }
    if (typeof expected !== 'string' || !expected || native.redirect_uri !== expected) {
      throw new Error('The registered Android callback does not match this app package');
    }
    options = { ...options, authConfig: { ...auth, frontend: {
      ...auth.frontend, client_id: native.client_id, redirect_uri: native.redirect_uri,
    } } };
  }
  const transport = createNativeAuthorizationTransport({ App, Browser });
  const receiptKey = `mozaiks:${encodeURIComponent(options.appId)}:native-callback-delivery`;
  async function digest(url) {
    const bytes = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(url));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  const adapter = createSharedAuthAdapter({
    ...options,
    authorizationTransport: {
      async open(request) {
        const callback = await transport.open(request);
        // A digest prevents redelivery of Android's retained launch intent after
        // a WebView reload. It contains no code/token and grants no identity.
        window.sessionStorage.setItem(receiptKey, await digest(callback));
        return callback;
      },
    },
  });
  // A retained intent can complete only a surviving PKCE transaction. In
  // particular, logout clears those transactions before navigating; reading
  // a native launch intent then cannot add identity and needlessly blocks boot.
  if (!adapter.hasPendingAuthorization()) return adapter;
  let launch;
  let launchTimer;
  try {
    launch = await Promise.race([
      Promise.resolve().then(() => App.getLaunchUrl()),
      new Promise(resolve => { launchTimer = setTimeout(() => resolve(undefined), 5000); }),
    ]);
  } catch {
    // A failed native bridge must leave fresh sign-in available. Pending PKCE
    // state stays with the shared adapter; a launch intent never grants identity.
    return adapter;
  } finally {
    clearTimeout(launchTimer);
  }
  if (launch?.url) {
    const receipt = await digest(launch.url);
    if (window.sessionStorage.getItem(receiptKey) !== receipt) {
      window.sessionStorage.setItem(receiptKey, receipt);
      let returnPath = adapter.routes.login;
      // The shared validator rejects a cold callback when its transaction was
      // lost with the process. A fresh sign-in remains available in that case.
      try { returnPath = (await adapter.handleCallback(launch.url)).returnPath; }
      catch { /* A launch intent alone cannot grant an authenticated session. */ }
      window.history.replaceState(window.history.state, '', returnPath);
      window.dispatchEvent(new window.PopStateEvent('popstate'));
    }
  }
  return adapter;
}
