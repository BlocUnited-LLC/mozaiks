import { useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import { useNavigation } from '../providers/NavigationProvider';
import { useTheme } from '../styles/useTheme';
import { Button } from '../ui/primitives/Button.jsx';
import { safeReturnPath } from './authAdapter.js';

function AuthCard({ title, children }) {
  useTheme();
  const { appName } = useNavigation();
  return (
    <main id="main-content" className="flex min-h-screen items-center justify-center bg-background px-6 py-12 text-foreground">
      <section className="w-full max-w-md rounded-xl border border-border bg-card p-8 shadow-sm" aria-labelledby="auth-title">
        <p className="mb-3 text-sm text-muted-foreground">{appName}</p>
        <h1 id="auth-title" className="mb-6 text-2xl font-semibold">{title}</h1>
        {children}
      </section>
    </main>
  );
}

export function LoginPage() {
  const { auth, user, loading } = useChatUI();
  const { navigation } = useNavigation();
  const location = useLocation();
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
      await auth.login({ returnPath });
    } catch (failure) {
      console.error('Sign-in could not start:', failure);
      setError('Sign-in could not start. Please try again.');
      setPending(false);
    }
  }

  return (
    <AuthCard title="Sign in">
      <p className="mb-6 text-sm text-muted-foreground">Continue with your account to access this app.</p>
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
