/**
 * ProfilePage — Social identity surface.
 *
 * Two views from one component:
 *   /me              — your own profile, editable
 *   /u/:username     — someone else's profile, read-only
 *
 * Layout: Hero (always framework-owned) + Tab bar (module-declared).
 * Modules contribute tabs via GET /api/me/profile-tabs.
 * Each tab declares: id, label, order, component, action.
 *
 * Shell mode: social — full-width, header on, no sidebar.
 *
 * See: docs/architecture/foundations/profile-panel-contract.md
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import componentRegistry from '../registry/componentRegistry';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getHostApiBaseUrl(config, api) {
  if (api && typeof api.getHttpBaseUrl === 'function') {
    const baseUrl = api.getHttpBaseUrl();
    if (typeof baseUrl === 'string') return baseUrl.replace(/\/+$/, '');
  }
  const configured = (
    config?.apiUrl ||
    config?.api_url ||
    config?.appBackendUrl ||
    config?.app_backend_url ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL) ||
    ''
  );
  return typeof configured === 'string' ? configured.replace(/\/+$/, '') : '';
}

async function fetchWithAuth(url, options = {}, auth = null) {
  const token = await auth?.getToken?.();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

function initials(name, email) {
  const src = name || email || '?';
  return src.split(/[\s.@_-]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
}

function formatJoined(iso) {
  if (!iso) return null;
  try { return new Date(iso).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }); }
  catch { return null; }
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
  );
}

function ErrorState({ message }) {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-6 py-4 text-sm text-destructive max-w-sm text-center">
        {message}
      </div>
    </div>
  );
}

function CameraIcon({ className = 'h-4 w-4' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function heroStorageKey(username, field) {
  return `mozaiks_profile_${field}_${username || 'me'}`;
}

function ProfileHero({ profile, isOwner, backendUrl, auth, onEdited }) {
  const coverInputRef = useRef(null);
  const avatarInputRef = useRef(null);
  const storageId = profile.username || 'me';

  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(profile.display_name || profile.username || '');
  const [bio, setBio] = useState(profile.bio || '');
  const [coverPreview, setCoverPreview] = useState(
    profile.cover_url || localStorage.getItem(heroStorageKey(storageId, 'cover')) || null
  );
  const [avatarPreview, setAvatarPreview] = useState(
    profile.avatar_url || localStorage.getItem(heroStorageKey(storageId, 'avatar')) || null
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const name = profile.display_name || profile.username || 'Unknown';
  const handle = profile.username ? `@${profile.username}` : null;
  const joined = formatJoined(profile.created_at);
  const roles = profile.roles || [];
  const avatarText = initials(name, profile.email);

  function handleCoverChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    fileToDataUrl(file).then((dataUrl) => {
      setCoverPreview(dataUrl);
      try { localStorage.setItem(heroStorageKey(storageId, 'cover'), dataUrl); } catch (_) {}
    });
    e.target.value = '';
  }

  function handleAvatarChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    fileToDataUrl(file).then((dataUrl) => {
      setAvatarPreview(dataUrl);
      try { localStorage.setItem(heroStorageKey(storageId, 'avatar'), dataUrl); } catch (_) {}
    });
    e.target.value = '';
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const body = { display_name: displayName, bio };
      const res = await fetchWithAuth(`${backendUrl}/api/me`, {
        method: 'PUT',
        body: JSON.stringify(body),
      }, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setEditing(false);
      onEdited?.();
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {/* Cover */}
      <div className="relative h-36 sm:h-48 w-full overflow-hidden bg-gradient-to-br from-primary/30 via-primary/10 to-secondary/20">
        {coverPreview && (
          <img src={coverPreview} alt="" className="absolute inset-0 h-full w-full object-cover" />
        )}
        {isOwner && (
          <>
            <button
              type="button"
              onClick={() => coverInputRef.current?.click()}
              className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-xl border border-white/20 bg-black/45 backdrop-blur-sm px-3 py-1.5 text-xs font-medium text-white hover:bg-black/65 transition-colors"
            >
              <CameraIcon className="h-3.5 w-3.5" />
              Change cover
            </button>
            <input
              ref={coverInputRef}
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={handleCoverChange}
            />
          </>
        )}
      </div>

      <div className="px-5 sm:px-8">
        {/* Avatar + actions row */}
        <div className="flex items-end justify-between gap-4 -mt-12">
          {/* Avatar */}
          <div className="relative group">
            <div className="h-24 w-24 rounded-full border-4 border-card overflow-hidden flex items-center justify-center bg-primary/15 shadow-lg flex-shrink-0">
              {avatarPreview ? (
                <img src={avatarPreview} alt={name} className="h-full w-full object-cover" />
              ) : (
                <span className="text-3xl font-bold text-primary">{avatarText}</span>
              )}
            </div>
            {isOwner && (
              <>
                <button
                  type="button"
                  onClick={() => avatarInputRef.current?.click()}
                  className="absolute inset-0 rounded-full flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Change photo"
                >
                  <CameraIcon className="h-6 w-6 text-white" />
                </button>
                <input
                  ref={avatarInputRef}
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={handleAvatarChange}
                />
              </>
            )}
          </div>

          <div className="pb-2 flex gap-2">
            {isOwner && !editing && (
              <button
                onClick={() => setEditing(true)}
                className="rounded-2xl border border-border bg-card px-4 py-1.5 text-sm font-semibold text-foreground hover:border-primary/40 hover:text-primary transition-colors"
              >
                Edit profile
              </button>
            )}
            {!isOwner && (
              <>
                <button className="rounded-2xl bg-primary px-4 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-colors">
                  Message
                </button>
                <button className="rounded-2xl border border-border bg-card px-4 py-1.5 text-sm font-semibold text-foreground hover:border-primary/40 hover:text-primary transition-colors">
                  Add contact
                </button>
              </>
            )}
          </div>
        </div>

        {/* Name + bio */}
        <div className="mt-4">
          {editing ? (
            <div className="space-y-3 max-w-md">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Display name</label>
                <input
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  placeholder="Display name"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Bio</label>
                <textarea
                  rows={2}
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                  value={bio}
                  onChange={e => setBio(e.target.value)}
                  placeholder="Tell people a bit about yourself"
                />
              </div>
              {saveError && <p className="text-xs text-destructive">{saveError}</p>}
              <div className="flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="rounded-xl bg-primary px-4 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={() => {
                    setEditing(false);
                    setDisplayName(profile.display_name || profile.username || '');
                    setBio(profile.bio || '');
                  }}
                  className="rounded-xl border border-border px-4 py-1.5 text-sm text-muted-foreground hover:bg-muted/50 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <h1 className="text-xl font-bold text-foreground">{name}</h1>
              {handle && <p className="text-sm text-muted-foreground">{handle}</p>}
              {profile.bio && <p className="mt-2 text-sm text-foreground/80 max-w-lg leading-relaxed">{profile.bio}</p>}
            </>
          )}

          {!editing && (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
              {joined && <span className="text-xs text-muted-foreground">Joined {joined}</span>}
              {roles.map(role => (
                <span key={role} className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${role === 'admin' ? 'bg-warning/15 text-warning' : 'bg-muted text-muted-foreground'}`}>
                  {role}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab bar + content
// ---------------------------------------------------------------------------

function TabBar({ tabs, activeId, onSelect }) {
  return (
    <div className="border-b border-border px-5 sm:px-8 mt-6">
      <nav className="flex gap-0 overflow-x-auto scrollbar-none">
        {tabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onSelect(tab.id)}
            className={[
              'relative shrink-0 px-4 py-3 text-sm font-medium transition-colors whitespace-nowrap',
              activeId === tab.id
                ? 'text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:rounded-full after:bg-primary'
                : 'text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            {tab.label}
          </button>
        ))}
      </nav>
    </div>
  );
}

function TabContent({ tab }) {
  if (!tab) return null;

  if (tab.error) {
    return (
      <div className="px-5 sm:px-8 py-8">
        <p className="text-sm text-muted-foreground italic">Could not load {tab.label}.</p>
      </div>
    );
  }

  const Component = tab.component ? componentRegistry.getComponent(tab.component) : null;

  if (Component) {
    return (
      <div className="px-5 sm:px-8 py-6">
        <Component tab={tab} data={tab.data} />
      </div>
    );
  }

  // Generic fallback — render key/value data
  if (tab.data && typeof tab.data === 'object') {
    const entries = Object.entries(tab.data).filter(([, v]) => v !== null && v !== undefined);
    if (entries.length > 0) {
      return (
        <div className="px-5 sm:px-8 py-6">
          <div className="divide-y divide-border rounded-2xl border border-border bg-card overflow-hidden">
            {entries.map(([k, v]) => (
              <div key={k} className="flex items-center justify-between px-5 py-3">
                <span className="text-sm text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</span>
                <span className="text-sm font-medium text-foreground">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      );
    }
  }

  return (
    <div className="px-5 sm:px-8 py-8">
      <p className="text-sm text-muted-foreground italic">Nothing here yet.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

const DEMO_PROFILE = {
  username: 'demo.user',
  display_name: 'Demo User',
  email: 'demo@mozaiks.app',
  bio: 'Building on Mozaiks.',
  roles: ['admin'],
  created_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * 180).toISOString(),
};

export default function ProfilePage() {
  const params = useParams();
  const username = params.username || null;
  const { config, auth, api } = useChatUI();
  const backendUrl = getHostApiBaseUrl(config, api);
  const isOwner = !username;

  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState(null);
  const [tabs, setTabs] = useState([]);
  const [tabsLoading, setTabsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState(null);

  const loadProfile = useCallback(async () => {
    if (!backendUrl) {
      setProfile(DEMO_PROFILE);
      setProfileLoading(false);
      return;
    }
    setProfileLoading(true);
    setProfileError(null);
    try {
      const endpoint = isOwner
        ? `${backendUrl}/api/me`
        : `${backendUrl}/api/users/${encodeURIComponent(username)}`;
      const res = await fetchWithAuth(endpoint, {}, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setProfile(await res.json());
    } catch (e) {
      if (isOwner) {
        setProfile(DEMO_PROFILE);
      } else {
        setProfileError(e.message);
      }
    } finally {
      setProfileLoading(false);
    }
  }, [backendUrl, auth, isOwner, username]);

  const loadTabs = useCallback(async () => {
    if (!backendUrl) { setTabsLoading(false); return; }
    setTabsLoading(true);
    try {
      const res = await fetchWithAuth(`${backendUrl}/api/me/profile-tabs`, {}, auth);
      const body = res.ok ? await res.json() : { tabs: [] };
      const raw = Array.isArray(body?.tabs) ? body.tabs : [];
      setTabs(raw);
      if (raw.length > 0) setActiveTab(prev => prev || raw[0].id);
    } catch {
      setTabs([]);
    } finally {
      setTabsLoading(false);
    }
  }, [backendUrl, auth]);

  useEffect(() => { loadProfile(); }, [loadProfile]);
  useEffect(() => { loadTabs(); }, [loadTabs]);

  if (profileLoading) return <Spinner />;
  if (profileError && !profile) return <ErrorState message={`Could not load profile: ${profileError}`} />;

  const currentTab = tabs.find(t => t.id === activeTab) || tabs[0] || null;

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl">
        <ProfileHero
          profile={profile}
          isOwner={isOwner}
          backendUrl={backendUrl}
          auth={auth}
          onEdited={loadProfile}
        />

        {!tabsLoading && tabs.length > 0 && (
          <>
            <TabBar tabs={tabs} activeId={currentTab?.id} onSelect={setActiveTab} />
            <TabContent tab={currentTab} />
          </>
        )}
      </div>
    </div>
  );
}
