/**
 * ProfilePage — user identity surface.
 *
 * Two views from one component:
 *   /me          — own profile, editable
 *   /u/:username — public profile, read-only
 *
 * Layout driven by GET /api/me/profile-config → layout field.
 * Pages contributed by modules via contracts/profile.yaml (v2 schema).
 * Page data is loaded from GET /api/me/profile-pages.
 *
 * Supported layouts: sidebar_left | top_nav | drawer | icon_rail
 * Shell mode: social — full-width, header on, no sidebar.
 *
 * See: docs/architecture/foundations/profile-panel-contract.md
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import componentRegistry from '../registry/componentRegistry';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SECTION_ORDER = ['overview', 'platform', 'social', 'settings'];
const SECTION_LABELS = {
  overview: '',
  platform: 'Platform',
  social: 'Social',
  settings: 'Settings',
};
const VALID_LAYOUTS = new Set(['sidebar_left', 'top_nav', 'drawer', 'icon_rail']);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getHostApiBaseUrl(config, api) {
  if (api && typeof api.getHttpBaseUrl === 'function') {
    const baseUrl = api.getHttpBaseUrl();
    if (typeof baseUrl === 'string' && baseUrl.trim()) return baseUrl.replace(/\/+$/, '');
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
  if (typeof configured === 'string' && configured.trim()) return configured.replace(/\/+$/, '');
  return typeof window !== 'undefined' ? window.location.origin : '';
}

async function fetchWithAuth(url, options = {}, auth = null) {
  const token = await auth?.getAccessToken?.();
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

function profileTrace(event, details = {}) {
  try { console.info('[mozaiks-profile]', event, details); } catch (_) {}
}

function profileWarn(event, details = {}) {
  try { console.warn('⚠️ [mozaiks-profile]', event, details); } catch (_) {}
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

const ICON_SHAPES = {
  user: <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></>,
  users: <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
  'message-circle': <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  activity: <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />,
  grid: <><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>,
  'credit-card': <><rect x="1" y="4" width="22" height="16" rx="2" ry="2" /><line x1="1" y1="10" x2="23" y2="10" /></>,
  layout: <><rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><line x1="3" y1="9" x2="21" y2="9" /><line x1="9" y1="21" x2="9" y2="9" /></>,
  star: <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />,
  menu: <><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></>,
  x: <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>,
};

function ProfileIcon({ name, className = 'h-4 w-4' }) {
  const shape = ICON_SHAPES[name] || <circle cx="12" cy="12" r="2" />;
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {shape}
    </svg>
  );
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

function ErrorState({ message, onRetry }) {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-6 py-4 text-sm text-destructive max-w-sm text-center">
        <p role="alert">{message}</p>
        {onRetry && <button type="button" onClick={onRetry} className="mt-3 rounded-lg border border-current px-3 py-1.5">Try again</button>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function ProfileHero({ profile, isOwner, backendUrl, auth, onEdited }) {
  const saveRequest = useRef(null);
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(profile.display_name || profile.username || '');
  const [bio, setBio] = useState(profile.bio || '');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const name = profile.display_name || profile.username || 'Unknown';
  const handle = profile.username ? `@${profile.username}` : null;
  const joined = formatJoined(profile.created_at);
  const roles = profile.roles || [];
  const avatarText = initials(name, profile.email);

  useEffect(() => () => saveRequest.current?.abort(), []);

  async function handleSave() {
    if (saveRequest.current) return;
    const controller = new AbortController();
    saveRequest.current = controller;
    setSaving(true);
    setSaveError(null);
    try {
      const body = { display_name: displayName, bio };
      const res = await fetchWithAuth(`${backendUrl}/api/me`, {
        method: 'PUT',
        body: JSON.stringify(body),
        signal: controller.signal,
      }, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      if (controller.signal.aborted) return;
      setEditing(false);
      onEdited?.();
    } catch (e) {
      if (!controller.signal.aborted) setSaveError(e.message);
    } finally {
      if (!controller.signal.aborted) {
        saveRequest.current = null;
        setSaving(false);
      }
    }
  }

  return (
    <div>
      {/* Cover */}
      <div className="relative h-36 sm:h-48 w-full overflow-hidden bg-gradient-to-br from-primary/30 via-primary/10 to-secondary/20">
        {profile.cover_url && (
          <img src={profile.cover_url} alt="" className="absolute inset-0 h-full w-full object-cover" />
        )}
      </div>

      <div className="px-5 sm:px-8">
        {/* Avatar + actions row */}
        <div className="flex items-end justify-between gap-4 -mt-12">
          <div className="relative group">
            <div className="h-24 w-24 rounded-full border-4 border-card overflow-hidden flex items-center justify-center bg-primary/15 shadow-lg flex-shrink-0">
              {profile.avatar_url ? (
                <img src={profile.avatar_url} alt={name} className="h-full w-full object-cover" />
              ) : (
                <span className="text-3xl font-bold text-primary">{avatarText}</span>
              )}
            </div>
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
// Page content renderer
// ---------------------------------------------------------------------------

function PageContent({ page, padded = true, isOwner = false }) {
  if (!page) return null;

  const Component = page.component ? componentRegistry.getComponent(page.component) : null;
  const cls = padded ? 'px-5 sm:px-8 py-6' : 'py-6';

  if (Component) {
    return (
      <div className={cls}>
        <Component page={page} tab={page} data={page.data} isOwner={isOwner} />
      </div>
    );
  }

  if (page.error) {
    return (
      <div className={cls}>
        <p className="text-sm text-muted-foreground italic">Could not load {page.label}.</p>
      </div>
    );
  }

  if (page.data && typeof page.data === 'object') {
    const entries = Object.entries(page.data).filter(([, v]) => v !== null && v !== undefined);
    if (entries.length > 0) {
      return (
        <div className={cls}>
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
    <div className={cls}>
      <p className="text-sm text-muted-foreground italic">Nothing here yet.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout: Top Nav
// Horizontal scrollable tabs below the hero. Default layout.
// ---------------------------------------------------------------------------

function TopNavLayout({ allPages, activePage, onSelect, isOwner }) {
  const visible = isOwner ? allPages : allPages.filter(p => p.visibility !== 'owner_only');
  if (!visible.length) return null;

  return (
    <>
      <div className="border-b border-border px-5 sm:px-8 mt-6">
        <nav className="flex gap-0 overflow-x-auto scrollbar-none">
          {visible.map(page => (
            <button
              key={page.id}
              type="button"
              onClick={() => onSelect(page.id)}
              className={[
                'relative shrink-0 px-4 py-3 text-sm font-medium transition-colors whitespace-nowrap',
                activePage?.id === page.id
                  ? 'text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:rounded-full after:bg-primary'
                  : 'text-muted-foreground hover:text-foreground',
              ].join(' ')}
            >
              {page.label}
            </button>
          ))}
        </nav>
      </div>
      <PageContent page={activePage} isOwner={isOwner} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Layout: Sidebar Left
// Left sidebar with section grouping. Enterprise / dashboard apps.
// ---------------------------------------------------------------------------

function SidebarLeftLayout({ pagesBySection, activePage, onSelect, isOwner }) {
  return (
    <div className="flex gap-6 mt-6 px-5 sm:px-8">
      <aside className="w-52 flex-shrink-0">
        {SECTION_ORDER.map(sectionId => {
          const pages = (pagesBySection[sectionId] || []).filter(
            p => isOwner || p.visibility !== 'owner_only',
          );
          if (!pages.length) return null;
          const label = SECTION_LABELS[sectionId];
          return (
            <div key={sectionId} className="mb-5">
              {label && (
                <p className="px-3 mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {label}
                </p>
              )}
              <div className="space-y-0.5">
                {pages.map(page => (
                  <button
                    key={page.id}
                    type="button"
                    onClick={() => onSelect(page.id)}
                    className={[
                      'w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg text-left transition-colors',
                      activePage?.id === page.id
                        ? 'bg-muted text-foreground font-medium'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
                    ].join(' ')}
                  >
                    {page.icon && (
                      <ProfileIcon name={page.icon} className="h-4 w-4 flex-shrink-0 opacity-70" />
                    )}
                    {page.label}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </aside>
      <div className="flex-1 min-w-0 py-6 border-l border-border pl-6">
        <PageContent page={activePage} padded={false} isOwner={isOwner} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout: Drawer
// Hamburger toggle that opens a full nav drawer. Mobile-first / simple apps.
// ---------------------------------------------------------------------------

function DrawerLayout({ allPages, pagesBySection, activePage, onSelect, isOwner }) {
  const [open, setOpen] = useState(false);

  const handleSelect = (id) => {
    onSelect(id);
    setOpen(false);
  };

  return (
    <div className="mt-6">
      {/* Trigger bar */}
      <div className="px-5 sm:px-8 mb-5">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground hover:border-primary/40 hover:text-primary transition-colors"
        >
          <ProfileIcon name="menu" className="h-4 w-4" />
          <span>{activePage?.label || 'Menu'}</span>
        </button>
      </div>

      {/* Drawer */}
      {open && (
        <div className="fixed inset-0 z-50 flex">
          <div
            className="absolute inset-0 bg-foreground/20 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          />
          <aside className="relative z-10 w-72 bg-card border-r border-border h-full overflow-y-auto shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-4 py-3.5 border-b border-border">
              <span className="text-sm font-semibold text-foreground">Navigation</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <ProfileIcon name="x" className="h-4 w-4" />
              </button>
            </div>
            <nav className="flex-1 p-3 space-y-4">
              {SECTION_ORDER.map(sectionId => {
                const pages = (pagesBySection[sectionId] || []).filter(
                  p => isOwner || p.visibility !== 'owner_only',
                );
                if (!pages.length) return null;
                const label = SECTION_LABELS[sectionId];
                return (
                  <div key={sectionId}>
                    {label && (
                      <p className="px-3 mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {label}
                      </p>
                    )}
                    <div className="space-y-0.5">
                      {pages.map(page => (
                        <button
                          key={page.id}
                          type="button"
                          onClick={() => handleSelect(page.id)}
                          className={[
                            'w-full flex items-center gap-2.5 px-3 py-2.5 text-sm rounded-lg text-left transition-colors',
                            activePage?.id === page.id
                              ? 'bg-muted text-foreground font-medium'
                              : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
                          ].join(' ')}
                        >
                          {page.icon && (
                            <ProfileIcon name={page.icon} className="h-4 w-4 flex-shrink-0 opacity-70" />
                          )}
                          {page.label}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </nav>
          </aside>
        </div>
      )}

      {/* Content */}
      <div className="px-5 sm:px-8">
        <PageContent page={activePage} padded={false} isOwner={isOwner} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout: Icon Rail
// Narrow icon-only sidebar with tooltips. Productivity / SaaS tools.
// ---------------------------------------------------------------------------

function IconRailLayout({ allPages, activePage, onSelect, isOwner }) {
  const visible = isOwner ? allPages : allPages.filter(p => p.visibility !== 'owner_only');

  return (
    <div className="flex mt-6">
      <aside className="w-14 border-r border-border flex-shrink-0 flex flex-col items-center gap-1 pt-2 pb-4">
        {visible.map(page => (
          <button
            key={page.id}
            type="button"
            onClick={() => onSelect(page.id)}
            title={page.label}
            className={[
              'h-10 w-10 flex items-center justify-center rounded-lg transition-colors',
              activePage?.id === page.id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted',
            ].join(' ')}
          >
            <ProfileIcon name={page.icon || 'user'} className="h-5 w-5" />
          </button>
        ))}
      </aside>
      <div className="flex-1 min-w-0 px-5 sm:px-8 py-6">
        <PageContent page={activePage} padded={false} isOwner={isOwner} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  const { username = null } = useParams();
  const { config, auth, api, user } = useChatUI();
  const backendUrl = getHostApiBaseUrl(config, api);
  const identity = user?.id || user?.user_id || user?.sub || '';
  // Changing the signed-in person or viewed profile clears all private content
  // and editing state before the next request can render.
  return <ProfileView key={JSON.stringify([backendUrl, identity, username])}
    username={username} backendUrl={backendUrl} auth={auth} />;
}

function ProfileView({ username, backendUrl, auth }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isOwner = !username;
  const profileRequest = useRef(null);
  const [reload, setReload] = useState(0);

  const searchParams = new URLSearchParams(location.search);
  const urlTabParam = searchParams.get('tab') || null;

  // Profile state
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState(null);

  // Layout + pages state
  const [layout, setLayout] = useState('top_nav');
  const [pagesBySection, setPagesBySection] = useState({});
  const [pagesLoading, setPagesLoading] = useState(true);
  const [pagesError, setPagesError] = useState(false);
  const [activePage, setActivePage] = useState(urlTabParam || null);

  // --- Data loading ---

  const loadProfile = useCallback(async () => {
    profileRequest.current?.abort();
    const controller = new AbortController();
    profileRequest.current = controller;
    setProfileLoading(true);
    setProfile(null);
    setProfileError(null);
    try {
      const endpoint = isOwner
        ? `${backendUrl}/api/me`
        : `${backendUrl}/api/users/${encodeURIComponent(username)}`;
      const res = await fetchWithAuth(endpoint, { signal: controller.signal }, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const body = await res.json();
      if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('Invalid profile response');
      if (![body.user_id, body.username].some(value => typeof value === 'string' && value.trim())) {
        throw new Error('Profile identity is missing');
      }
      if (!controller.signal.aborted) setProfile(body);
    } catch (e) {
      if (!controller.signal.aborted) setProfileError(e.message);
    } finally {
      if (!controller.signal.aborted) setProfileLoading(false);
    }
  }, [backendUrl, auth, isOwner, username]);

  const loadConfig = useCallback(async (signal) => {
    try {
      const res = await fetchWithAuth(`${backendUrl}/api/me/profile-config`, { signal }, auth);
      if (res.ok) {
        const body = await res.json();
        const lyt = body?.layout;
        if (!signal.aborted && typeof lyt === 'string' && VALID_LAYOUTS.has(lyt)) {
          setLayout(lyt);
          profileTrace('config:layout', { layout: lyt });
        }
      }
    } catch (_) {}
  }, [backendUrl, auth]);

  const loadPages = useCallback(async (signal) => {
    setPagesLoading(true);
    setPagesError(false);
    setPagesBySection({});
    let sections = {};

    const subjectParams = new URLSearchParams();
    if (!isOwner && username) subjectParams.set('username', username);
    const subjectSuffix = subjectParams.toString() ? `?${subjectParams}` : '';

    try {
      const res = await fetchWithAuth(`${backendUrl}/api/me/profile-pages${subjectSuffix}`, { signal }, auth);
      if (!res.ok) throw new Error('Profile sections unavailable');
      const body = await res.json();
      const validPages = pages => Array.isArray(pages) && pages.every(page =>
        page && typeof page === 'object' && !Array.isArray(page)
        && typeof page.id === 'string' && page.id.trim());
      if (body?.sections && !Array.isArray(body.sections) && typeof body.sections === 'object') {
        if (!Object.values(body.sections).every(validPages)) throw new Error('Invalid profile sections');
        sections = body.sections;
        profileTrace('pages:v2:sections', { sectionIds: Object.keys(sections) });
      } else if (Array.isArray(body?.pages)) {
        if (!validPages(body.pages)) throw new Error('Invalid profile pages');
        for (const page of body.pages) {
          const s = page.section || 'overview';
          if (!sections[s]) sections[s] = [];
          sections[s].push(page);
        }
        profileTrace('pages:v2:flat', { total: body.pages.length });
      } else {
        throw new Error('Profile sections are missing');
      }
    } catch (_) {
      if (!signal.aborted) {
        setPagesError(true);
        profileWarn('pages:v2:error', {});
      }
    }

    if (!signal.aborted) {
      setPagesBySection(sections);
      setPagesLoading(false);
    }
  }, [backendUrl, auth, isOwner, username]);

  useEffect(() => {
    loadProfile();
    return () => profileRequest.current?.abort();
  }, [loadProfile, reload]);
  useEffect(() => {
    const controller = new AbortController();
    loadConfig(controller.signal);
    return () => controller.abort();
  }, [loadConfig, reload]);
  useEffect(() => {
    const controller = new AbortController();
    loadPages(controller.signal);
    return () => controller.abort();
  }, [loadPages, reload]);

  // --- Derived state ---

  const allPages = useMemo(() => {
    const flat = SECTION_ORDER.flatMap(s => pagesBySection[s] || []);
    // Include any unknown sections not in SECTION_ORDER
    for (const [s, pages] of Object.entries(pagesBySection)) {
      if (!SECTION_ORDER.includes(s)) flat.push(...(pages || []));
    }
    return isOwner ? flat : flat.filter(page => page.visibility !== 'owner_only');
  }, [pagesBySection, isOwner]);

  // Sync active page when pages load or URL param changes
  useEffect(() => {
    if (!allPages.length) { setActivePage(null); return; }
    setActivePage(prev => {
      if (urlTabParam && allPages.some(p => p.id === urlTabParam)) return urlTabParam;
      if (prev && allPages.some(p => p.id === prev)) return prev;
      profileTrace('pages:active:fallback', { next: allPages[0]?.id });
      return allPages[0]?.id || null;
    });
  }, [allPages, urlTabParam]);

  const currentPage = useMemo(
    () => allPages.find(p => p.id === activePage) || allPages[0] || null,
    [allPages, activePage],
  );

  const handleSelect = (id) => {
    setActivePage(id);
    const p = new URLSearchParams(location.search);
    p.set('tab', id);
    navigate(`${location.pathname}?${p.toString()}`, { replace: true });
  };

  // --- Render ---

  if (profileLoading) return <Spinner />;
  if (profileError || !profile) return <ErrorState message="Could not load this profile. Please try again."
    onRetry={() => setReload(value => value + 1)} />;

  // Align to the shared content measure (--mz-content-max, theme-configurable
  // per app) so this route fills wide viewports like the workspace surfaces
  // instead of stranding large empty gutters.
  const containerClass = 'mx-auto w-full max-w-content px-4 pb-16 md:px-6 lg:px-8';

  const layoutProps = { allPages, pagesBySection, activePage: currentPage, onSelect: handleSelect, isOwner };

  return (
    <div className="min-h-screen bg-background">
      <div className={containerClass}>
        <ProfileHero
          profile={profile}
          isOwner={isOwner}
          backendUrl={backendUrl}
          auth={auth}
          onEdited={loadProfile}
        />

        {pagesError && <ErrorState message="Could not load profile sections."
          onRetry={() => setReload(value => value + 1)} />}
        {!pagesLoading && allPages.length > 0 && (
          <>
            {layout === 'top_nav'      && <TopNavLayout {...layoutProps} />}
            {layout === 'sidebar_left' && <SidebarLeftLayout {...layoutProps} />}
            {layout === 'drawer'       && <DrawerLayout {...layoutProps} />}
            {layout === 'icon_rail'    && <IconRailLayout {...layoutProps} />}
          </>
        )}
      </div>
    </div>
  );
}
