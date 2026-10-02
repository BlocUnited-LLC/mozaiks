import { useEffect, useState } from 'react';
import {
  MozaiksApp,
  WebSocketApiAdapter,
  componentRegistry,
  loadShellAuth,
  LoginPage,
  AuthCallbackPage,
} from '@mozaiks/chat-ui';
import * as platformExtensions from '@platform/extensions';

const apiBaseUrl = import.meta.env.VITE_API_URL ?? '';
const wsBaseUrl = import.meta.env.VITE_WS_URL || (apiBaseUrl ? apiBaseUrl.replace(/^http/, 'ws') : undefined);
const isDraftPreview = import.meta.env.VITE_MOZAIKS_PREVIEW === 'true';
let bootstrapPromise;

function bootstrap() {
  if (!bootstrapPromise) {
    bootstrapPromise = loadShellAuth({
      apiBaseUrl,
      createAppAuthAdapter: Reflect.get(platformExtensions, 'createAuthAdapter'),
      env: import.meta.env,
    }).then(({ authAdapter, shellConfig }) => {
      window.mozaiksAuth = authAdapter;
      // App initialization runs only after the host's authentication bootstrap.
      const register = Reflect.get(platformExtensions, 'register');
      if (register !== undefined) {
        if (typeof register !== 'function') throw new Error('App UI register export must be a function');
        register(componentRegistry.registerComponent.bind(componentRegistry));
      }
      if (!componentRegistry.hasComponent('LoginPage')) componentRegistry.registerComponent('LoginPage', LoginPage);
      if (!componentRegistry.hasComponent('AuthCallbackPage')) componentRegistry.registerComponent('AuthCallbackPage', AuthCallbackPage);
      const apiAdapter = new WebSocketApiAdapter({
        baseUrl: apiBaseUrl,
        ...(wsBaseUrl ? { wsUrl: wsBaseUrl } : {}),
        auth: authAdapter,
      });
      return { authAdapter, shellConfig, apiAdapter };
    }).catch(error => { bootstrapPromise = undefined; throw error; });
  }
  return bootstrapPromise;
}

export default function App() {
  const [state, setState] = useState(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    bootstrap().then(value => { if (!cancelled) setState(value); }).catch(error => {
      console.error('Application bootstrap failed:', error);
      if (!cancelled) setFailed(true);
    });
    return () => { cancelled = true; };
  }, [attempt]);

  return <>
    {isDraftPreview && (
      <div
        role="note"
        aria-label="Draft app preview"
        style={{
          position: 'fixed', left: 8, bottom: 'calc(5rem + env(safe-area-inset-bottom, 0px))',
          zIndex: 1000, pointerEvents: 'none', padding: '4px 8px', borderRadius: 6,
          background: '#0f172a', color: '#f8fafc', border: '1px solid #64748b',
          font: '600 11px/1.4 system-ui, sans-serif',
        }}
      >
        Draft preview
      </div>
    )}
    {!state ? (
    <main id="main-content" className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
      <div className="max-w-md text-center">
        {failed ? <>
          <h1 className="mb-3 text-xl font-semibold">Unable to open this app</h1>
          <p role="alert" className="mb-6 text-muted-foreground">The app could not load its sign-in settings. Please try again.</p>
          <button type="button" className="rounded-md bg-primary px-5 py-2 text-primary-foreground" onClick={() => setAttempt(value => value + 1)}>Try again</button>
        </> : <p role="status">Loading app…</p>}
      </div>
    </main>
    ) : <MozaiksApp {...state} />}
  </>;
}
