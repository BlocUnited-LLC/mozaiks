// Finite metadata only: no request URLs, headers, bodies, callbacks or errors.
export function observeBootstrap(page, { backendOrigin, webviewOrigin }) {
  const evidence = { shell_config: [], navigations: [] };
  const pending = new Map();
  page.on('request', request => {
    let url;
    try { url = new URL(request.url()); } catch { return; }
    if (url.origin !== backendOrigin || url.pathname !== '/api/shell-config') return;
    if (evidence.shell_config.length >= 12) return;
    const entry = { sequence: evidence.shell_config.length + 1, response_status: null, finished: false, failed: false };
    evidence.shell_config.push(entry);
    pending.set(request, entry);
  });
  page.on('response', response => {
    const entry = pending.get(response.request());
    if (entry) entry.response_status = response.status();
  });
  page.on('requestfinished', request => {
    const entry = pending.get(request);
    if (entry) entry.finished = true;
    pending.delete(request);
  });
  page.on('requestfailed', request => {
    const entry = pending.get(request);
    if (entry) entry.failed = true;
    pending.delete(request);
  });
  page.on('framenavigated', frame => {
    if (frame !== page.mainFrame() || evidence.navigations.length >= 12) return;
    let url;
    try { url = new URL(frame.url()); } catch { return; }
    evidence.navigations.push({
      bundled_origin: url.origin === webviewOrigin,
      route: url.origin === webviewOrigin && ['/login', '/community'].includes(url.pathname) ? url.pathname : 'other',
    });
  });
  return evidence;
}

// This function executes in the real WebView only after a failure. The native
// read-only state probe has its own bound and cannot extend the acceptance gate.
export async function inspectBootstrapFailure() {
  const capacitor = window.Capacitor;
  const snapshot = {
    document_ready_state: document.readyState,
    document_time_origin: performance.timeOrigin,
    has_capacitor: Boolean(capacitor),
    native_platform: capacitor?.getPlatform?.() === 'android',
    native_app_plugin: Boolean(capacitor?.Plugins?.App),
    native_state_probe: 'unavailable',
  };
  const app = capacitor?.Plugins?.App;
  if (typeof app?.getState !== 'function') return snapshot;
  let timer;
  try {
    snapshot.native_state_probe = await Promise.race([
      Promise.resolve().then(() => app.getState()).then(state => state?.isActive === true ? 'active' : 'inactive', () => 'rejected'),
      new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 1500); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
  return snapshot;
}
