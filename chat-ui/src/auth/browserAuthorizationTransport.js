const CALLBACK_MESSAGE = 'mozaiks.oidc.callback';
const POPUP_TRANSACTION = 'mozaiks.oidc.popup';
let relayedCallback = null;

/** Browser interaction only. The auth adapter owns PKCE and token verification. */
export function createBrowserAuthorizationTransport() {
  let popup;
  let finish;

  function cancel() {
    if (finish) finish(new Error('Sign-in was cancelled. Please try again.'));
    if (popup && !popup.closed) popup.close();
    popup = null;
  }

  return {
    kind: 'browser',
    prepare() {
      if (popup && !popup.closed) throw new Error('Authorization is already in progress');
      popup = window.open('about:blank', '_blank', 'popup,width=520,height=720');
      if (!popup) throw new Error('Allow the sign-in popup and try again.');
    },
    open({ url, callbackUri }) {
      if (!popup || popup.closed) return Promise.reject(new Error('The sign-in popup was closed.'));
      const callback = new URL(callbackUri);
      if (callback.origin !== window.location.origin) {
        cancel();
        return Promise.reject(new Error('Authorization callback must belong to this application.'));
      }
      const expectedState = new URL(url).searchParams.get('state');
      return new Promise((resolve, reject) => {
        const ownedPopup = popup;
        const complete = (error, value) => {
          window.removeEventListener('message', receive);
          window.clearInterval(closedCheck);
          window.clearTimeout(timeout);
          finish = null;
          if (!ownedPopup.closed) ownedPopup.close();
          popup = null;
          if (error) reject(error);
          else resolve(value);
        };
        const receive = event => {
          if (event.source !== ownedPopup || event.origin !== callback.origin
              || event.data?.type !== CALLBACK_MESSAGE || typeof event.data.url !== 'string') return;
          let returned;
          try { returned = new URL(event.data.url); } catch { return; }
          if (returned.origin !== callback.origin || returned.pathname !== callback.pathname
              || returned.hash || returned.searchParams.get('state') !== expectedState) return;
          complete(null, event.data.url);
        };
        const closedCheck = window.setInterval(() => {
          if (ownedPopup.closed) complete(new Error('Sign-in was cancelled. Please try again.'));
        }, 250);
        const timeout = window.setTimeout(() => complete(new Error('Sign-in expired. Please try again.')), 15 * 60 * 1000);
        finish = complete;
        window.addEventListener('message', receive);
        ownedPopup.sessionStorage.setItem(POPUP_TRANSACTION, JSON.stringify({
          callbackUri, state: expectedState, createdAt: Date.now(),
        }));
        ownedPopup.location.replace(url);
      });
    },
    cancel,
  };
}

export function relayBrowserAuthorization(callbackPath) {
  if (!window.opener || window.location.pathname !== callbackPath) return false;
  if (relayedCallback === window.location.href) return true;
  let transaction;
  try { transaction = JSON.parse(window.sessionStorage.getItem(POPUP_TRANSACTION)); } catch { return false; }
  const url = new URL(window.location.href);
  if (!transaction || transaction.callbackUri !== url.origin + url.pathname || url.hash
      || url.searchParams.getAll('state').length !== 1 || url.searchParams.get('state') !== transaction.state
      || !Number.isFinite(transaction.createdAt) || transaction.createdAt > Date.now()
      || Date.now() - transaction.createdAt > 15 * 60 * 1000) return false;
  window.sessionStorage.removeItem(POPUP_TRANSACTION);
  relayedCallback = window.location.href;
  window.opener.postMessage({ type: CALLBACK_MESSAGE, url: window.location.href }, window.location.origin);
  // The opener closes this window after receiving its bound callback message.
  return true;
}
