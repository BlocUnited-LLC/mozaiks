/**
 * ProfilePage — First-class user profile page.
 *
 * Registered in coreComponents.js. Visible when the app has auth enabled.
 * Shell-config controls nav visibility via show_when: auth_enabled.
 *
 * Calls app_backend_url/api/me for profile data.
 * app_backend_url is read from ChatUIProvider's uiConfig.appBackendUrl,
 * falling back to VITE_APP_BACKEND_URL, falling back to null (no-backend state).
 */

import { useState, useEffect, useCallback } from 'react';
import { useChatUI } from '../context/ChatUIContext';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getAppBackendUrl(config) {
  return (
    config?.appBackendUrl ||
    config?.app_backend_url ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_APP_BACKEND_URL) ||
    null
  );
}

async function fetchWithAuth(url, options = {}, auth = null) {
  const token = await auth?.getToken?.();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

// ---------------------------------------------------------------------------
// Primitive UI
// ---------------------------------------------------------------------------

function SectionHeading({ children }) {
  return (
    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-widest mt-6 mb-3">
      {children}
    </h2>
  );
}

function Badge({ children, variant = 'default' }) {
  const styles = {
    default: 'bg-muted text-muted-foreground',
    success: 'bg-success/20 text-success',
    primary: 'bg-primary/20 text-primary',
    warning: 'bg-warning/20 text-warning',
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[variant]}`}>
      {children}
    </span>
  );
}

function ErrorBox({ message }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-sm">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading…
    </div>
  );
}

function Avatar({ name, email }) {
  const initial = (name || email || '?')[0].toUpperCase();
  return (
    <div className="h-16 w-16 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center">
      <span className="text-2xl font-bold text-primary">{initial}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panels
// ---------------------------------------------------------------------------

function ProfilePanel({ backendUrl, auth }) {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`${backendUrl}/api/me`, {}, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setProfile(data);
      setDisplayName(data.display_name || data.username || '');
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [backendUrl, auth]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetchWithAuth(`${backendUrl}/api/me`, {
        method: 'PUT',
        body: JSON.stringify({ display_name: displayName }),
      }, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const updated = await res.json();
      setProfile(updated);
      setEditing(false);
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={`Could not load profile: ${error}`} />;

  return (
    <div className="rounded-xl border border-border bg-card p-6">
      <div className="flex items-start gap-5">
        <Avatar name={profile.display_name || profile.username} email={profile.email} />
        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="flex flex-col gap-3">
              <input
                className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground w-full max-w-xs focus:outline-none focus:ring-2 focus:ring-primary/40"
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                placeholder="Display name"
              />
              {saveError && <ErrorBox message={saveError} />}
              <div className="flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={() => { setEditing(false); setDisplayName(profile.display_name || profile.username || ''); }}
                  className="rounded-lg border border-border px-4 py-1.5 text-sm text-muted-foreground hover:bg-muted/50 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-xl font-semibold text-foreground">
                {profile.display_name || profile.username || 'Unknown User'}
              </h2>
              <button
                onClick={() => setEditing(true)}
                className="text-xs text-primary hover:text-primary/80 transition-colors"
              >
                Edit
              </button>
            </div>
          )}
          <p className="text-sm text-muted-foreground mt-1">{profile.email}</p>
          <div className="flex gap-2 mt-2 flex-wrap">
            {profile.subscription_tier && (
              <Badge variant="primary">{profile.subscription_tier}</Badge>
            )}
            {(profile.roles || []).map(role => (
              <Badge key={role} variant={role === 'admin' ? 'warning' : 'default'}>{role}</Badge>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-3 border-t border-border pt-5">
        <div>
          <span className="text-xs text-muted-foreground uppercase tracking-wide block">Username</span>
          <span className="text-sm text-foreground font-medium">{profile.username || '—'}</span>
        </div>
        <div>
          <span className="text-xs text-muted-foreground uppercase tracking-wide block">Member since</span>
          <span className="text-sm text-foreground font-medium">
            {profile.created_at ? new Date(profile.created_at).toLocaleDateString() : '—'}
          </span>
        </div>
        <div>
          <span className="text-xs text-muted-foreground uppercase tracking-wide block">Last login</span>
          <span className="text-sm text-foreground font-medium">
            {profile.last_login_at ? new Date(profile.last_login_at).toLocaleString() : '—'}
          </span>
        </div>
      </div>
    </div>
  );
}

function PreferencesPanel({ backendUrl, auth }) {
  const [prefs, setPrefs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetchWithAuth(`${backendUrl}/api/me/preferences`, {}, auth);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        setPrefs(await res.json());
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [backendUrl, auth]);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={`Could not load preferences: ${error}`} />;

  const entries = Object.entries(prefs?.settings || {});
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground italic">No preferences configured for this app.</p>;
  }

  return (
    <div className="rounded-xl border border-border bg-card divide-y divide-border">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-center justify-between px-5 py-3">
          <span className="text-sm text-foreground">{key}</span>
          <span className="text-sm text-muted-foreground font-mono">{String(value)}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  const { user, config, auth } = useChatUI();
  const backendUrl = getAppBackendUrl(config);

  if (!backendUrl) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-border bg-card p-8 text-center max-w-sm">
          <h1 className="text-lg font-semibold text-foreground mb-2">No Backend Connected</h1>
          <p className="text-sm text-muted-foreground">
            This app does not have a connected app backend. Profile features require a backend instance.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-2xl">

        <div className="mb-8">
          <h1 className="text-2xl font-bold text-foreground">Profile</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {user?.email || user?.name || 'Your account'}
          </p>
        </div>

        <ProfilePanel backendUrl={backendUrl} auth={auth} />

        <SectionHeading>Preferences</SectionHeading>
        <PreferencesPanel backendUrl={backendUrl} auth={auth} />

      </div>
    </div>
  );
}
