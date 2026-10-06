import { createNativeAuthorizationTransport } from './authorizationTransport.mjs';

export async function createNativeAppAuthAdapter(options, { App, Browser, createSharedAuthAdapter, platform, window }) {
  if (platform !== 'android') throw new Error('This mobile reference requires Android');
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
  const launch = await App.getLaunchUrl();
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
