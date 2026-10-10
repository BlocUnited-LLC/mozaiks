/** Android browser navigation only. The shared auth adapter owns OAuth validation. */
export function createNativeAuthorizationTransport({ App, Browser, timeoutMs = 15 * 60 * 1000 }) {
  let active = false;
  return {
    async open({ url, callbackUri }) {
      if (active) throw new Error('A sign-in or sign-out is already in progress');
      active = true;
      let settled = false;
      let disposed = false;
      let timer;
      let finish;
      const handles = [];
      const completed = new Promise((resolve, reject) => {
        finish = (error, value) => {
          if (settled) return;
          settled = true;
          if (error) reject(error); else resolve(value);
        };
      });
      // Native events may arrive while listener registration is still awaiting IPC.
      completed.catch(() => {});
      timer = setTimeout(() => finish(new Error('Browser sign-in or sign-out expired')), timeoutMs);
      async function register(registration) {
        const handle = await registration;
        if (disposed) await handle.remove(); else handles.push(handle);
      }
      try {
        const navigate = async () => {
          await register(App.addListener('appUrlOpen', event => {
            if (typeof event.url !== 'string' || event.url.includes('#')) return;
            if (event.url.split('?')[0] === callbackUri) finish(null, event.url);
          }));
          if (settled) return completed;
          await register(Browser.addListener('browserFinished', () => {
            finish(new Error('Browser sign-in or sign-out was cancelled'));
          }));
          if (settled) return completed;
          await Browser.open({ url });
          return completed;
        };
        return await Promise.race([completed, navigate()]);
      } finally {
        disposed = true;
        clearTimeout(timer);
        // The Android Custom Tab can leave BrowserControllerActivity in the
        // task after its deep link returns to the app. A later open with
        // CLEAR_TOP can otherwise reach that activity without launching a tab.
        try {
          // A failed close must not discard a callback that OAuth has already
          // validated. Listener cleanup is attempted even when close fails.
          try { await Browser.close(); } catch { /* best effort */ }
          await Promise.allSettled(handles.map(handle => handle.remove()));
        } finally { active = false; }
      }
    },
  };
}
