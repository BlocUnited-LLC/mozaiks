import { useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import { useNavigation } from '../providers/NavigationProvider';
import { useTheme } from '../styles/useTheme';
import { Button } from '../ui/primitives/Button.jsx';
import { safeReturnPath } from './authAdapter.js';
import { authFetch } from '../adapters/api.js';

function AuthCard({ title, children }) {
  useTheme();
  const { appName } = useNavigation();
  return (
    <main id="main-content" className="flex min-h-screen items-center justify-center bg-background px-6 py-12 text-foreground">
      <section className="w-full max-w-md rounded-xl border border-border bg-card p-8 shadow-sm" aria-labelledby="auth-title">
        <p className="mb-3 text-sm text-muted-foreground">{appName}</p>
        <h1 id="auth-title" className="mb-6 text-2xl font-semibold heading-font">{title}</h1>
        {children}
      </section>
    </main>
  );
}

export function LoginPage() {
  const location = useLocation();
  return new URLSearchParams(location.search).get('owner') === '1' ? <OwnerAccountSetup /> : <SignInPage />;
}

function OwnerAccountSetup() {
  const { auth, user, loading, api, config } = useChatUI();
  const { navigation } = useNavigation();
  const navigate = useNavigate();
  const routes = navigation.auth?.contract?.routes;
  const [status, setStatus] = useState('checking');
  const [retry, setRetry] = useState(0);
  const started = useRef(false);
  const signingIn = useRef(false);
  const identity = user?.id || user?.user_id || user?.sub;
  const scope = JSON.stringify([identity, config?.appId || config?.app_id, api?.getHttpBaseUrl?.()]);
  const [verifiedScope, setVerifiedScope] = useState(null);
  const ready = status === 'ready' && verifiedScope === scope;

  async function signIn(reauthenticate = false) {
    if (signingIn.current) return;
    signingIn.current = true;
    setStatus('signing_in');
    try {
      const returnPath = `${routes.login}?owner=1`;
      const result = await auth.login({ returnPath, reauthenticate });
      if (result?.returnPath) navigate(safeReturnPath(result.returnPath, returnPath), { replace: true });
    } catch {
      setStatus('sign_in_failed');
    } finally {
      signingIn.current = false;
    }
  }

  useEffect(() => {
    if (user) { started.current = false; return; }
    if (loading || started.current) return;
    started.current = true;
    signIn();
  }, [loading, user]);

  useEffect(() => {
    if (loading || !user) return undefined;
    const controller = new AbortController();
    setStatus('checking');
    setVerifiedScope(null);
    authFetch('/api/admin/config', { signal: controller.signal }, {
      ...config, auth, baseUrl: api?.getHttpBaseUrl?.(),
    }).then(async response => {
      if (response.status === 403) return 'account_required';
      if (!response.ok) throw new Error('Owner access check failed');
      const body = await response.json();
      if (!body || !Array.isArray(body.pages)) throw new Error('Invalid owner access response');
      return 'ready';
    }).then(value => {
      if (!controller.signal.aborted) {
        setVerifiedScope(scope);
        setStatus(value);
      }
    }).catch(() => { if (!controller.signal.aborted) setStatus('check_failed'); });
    return () => controller.abort();
  }, [scope, loading, user, auth, api, config, retry]);

  return <AuthCard title={ready ? 'Owner access confirmed' : 'Set up your owner account'}>
    {ready ? <>
      <p className="mb-6 text-sm text-muted-foreground">This account can administer this app.</p>
      <Button className="w-full" onClick={() => navigate('/admin')}>Open Admin</Button>
      <Button className="mt-3 w-full" variant="secondary" onClick={() => navigate(routes.post_login_default)}>Open app</Button>
    </> : status === 'account_required' ? <>
      <p role="alert" className="mb-6 text-sm">This account has no Admin access. Sign in with the designated owner account and verify its email through your sign-in service.</p>
      <Button onClick={() => signIn(true)}>Sign in again</Button>
    </> : ['sign_in_failed', 'check_failed'].includes(status) ? <>
      <p role="alert" className="mb-6 text-sm">{status === 'sign_in_failed' ? 'Sign-in could not start.' : 'Owner access could not be checked.'} Please try again.</p>
      <Button onClick={status === 'sign_in_failed' ? () => signIn(Boolean(user)) : () => setRetry(value => value + 1)}>Try again</Button>
    </> : <p role="status" className="text-sm text-muted-foreground">{user ? 'Checking your Admin access…' : 'Continuing to your sign-in service. Create an account there if you need one, then verify your email.'}</p>}
  </AuthCard>;
}

function SignInPage() {
  const { auth, user, loading } = useChatUI();
  const { navigation } = useNavigation();
  const location = useLocation();
  const navigate = useNavigate();
  const contract = navigation.auth?.contract;
  const fallback = contract?.routes?.post_login_default || '/';
  const returnPath = safeReturnPath(new URLSearchParams(location.search).get('returnTo'), fallback);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const label = contract?.login_methods?.find(method => method.kind === 'oidc_redirect' && method.primary)?.label || 'Sign in';

  if (!loading && user) return <Navigate to={returnPath} replace />;

  async function signIn() {
    setPending(true);
    setError('');
    try {
      const result = await auth.login({ returnPath });
      if (result?.returnPath) navigate(safeReturnPath(result.returnPath, fallback), { replace: true });
    } catch (failure) {
      console.error('Sign-in could not start:', failure);
      setError('Sign-in could not start. Please try again.');
      setPending(false);
    }
  }

  return (
    <AuthCard title="Sign in">
      <p className="mb-6 text-sm text-muted-foreground">{contract?.signup_enabled ? 'Sign in or create an account to use this app.' : 'Continue with your account to access this app.'}</p>
      {error && <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}
      <Button className="w-full" onClick={signIn} disabled={loading || pending}>{pending ? 'Redirecting…' : label}</Button>
    </AuthCard>
  );
}

export function AuthCallbackPage() {
  const { auth } = useChatUI();
  const { navigation } = useNavigation();
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const routes = navigation.auth?.contract?.routes;

  useEffect(() => {
    let cancelled = false;
    async function complete() {
      try {
        if (typeof auth?.handleCallback !== 'function') throw new Error('This app has no sign-in callback handler');
        const result = await auth.handleCallback();
        if (!cancelled) navigate(safeReturnPath(result?.returnPath, routes?.post_login_default || '/'), { replace: true });
      } catch (failure) {
        console.error('Sign-in callback failed:', failure);
        if (!cancelled) setError('Sign-in could not be completed. Please restart sign-in.');
      }
    }
    complete();
    return () => { cancelled = true; };
  }, [auth, navigate, routes?.post_login_default]);

  return (
    <AuthCard title={error ? 'Sign-in unsuccessful' : 'Completing sign-in'}>
      {error ? <>
        <p role="alert" className="mb-6 text-sm text-destructive">{error}</p>
        <Button className="w-full" onClick={() => navigate(routes?.login || '/login', { replace: true })}>Return to sign in</Button>
      </> : <p role="status" className="text-sm text-muted-foreground">Verifying your account…</p>}
    </AuthCard>
  );
}
