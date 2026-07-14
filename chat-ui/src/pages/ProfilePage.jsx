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
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import componentRegistry from '../registry/componentRegistry';
import { ChatThread } from '../ui/primitives/index.js';

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
// User Messages / Support Inbox tab
// ---------------------------------------------------------------------------
// DM demo data — placeholder until a messaging module is wired.
// Support tickets are owned by workspace_support/contracts/profile.yaml
// and rendered by UserSupportPanel via the profile-panels API.
// ---------------------------------------------------------------------------

const DEMO_DMS = [
  {
    id: 'dm-alex',
    name: 'Alex Rivera',
    initials: 'AR',
    meta: 'Last seen 10 min ago',
    preview: 'Did you check out the new workflow I shared?',
    updatedAt: '10m',
    messages: [
      { role: 'peer', content: 'Hey! Did you check out the new workflow I pushed to the sandbox?', senderLabel: 'Alex', avatarText: 'AR' },
      { role: 'user', content: "Not yet, just wrapped up a build. What's it do?" },
      { role: 'peer', content: "It automates the whole content pipeline — from brief to published post. Cut our turnaround from 2 days to 45 min.", senderLabel: 'Alex', avatarText: 'AR' },
      { role: 'user', content: 'Seriously? I need to see this. Can you share the config?' },
      { role: 'peer', content: "Just dropped it in the shared workspace under /workflows/content-autopilot. Let me know what you think.", senderLabel: 'Alex', avatarText: 'AR' },
      { role: 'user', content: 'On it now. This is going to save us so much time.' },
    ],
  },
  {
    id: 'dm-sam',
    name: 'Sam Chen',
    initials: 'SC',
    meta: 'Last seen yesterday',
    preview: "Let me know when you're free to review",
    updatedAt: '1d',
    messages: [
      { role: 'user', content: 'Sam — are you around to do a quick code review on the new module?' },
      { role: 'peer', content: 'Sure, I have 30 min at 3pm. Send me the PR link.', senderLabel: 'Sam', avatarText: 'SC' },
      { role: 'user', content: "It's #247 on the main repo. It's pretty small." },
      { role: 'peer', content: "Left some comments — handler looks clean. Just had a question on the scoping in policy.py.", senderLabel: 'Sam', avatarText: 'SC' },
      { role: 'user', content: 'Good catch, updating it now.' },
      { role: 'peer', content: "Let me know when you're free to review the updated version.", senderLabel: 'Sam', avatarText: 'SC' },
    ],
  },
];

// ---------------------------------------------------------------------------
// Direct messages tab — DM list + thread view
// ---------------------------------------------------------------------------

function UserMessagesTab() {
  const [selectedId, setSelectedId] = useState(DEMO_DMS[0].id);
  const selected = DEMO_DMS.find(d => d.id === selectedId) || DEMO_DMS[0];

  return (
    <div className="flex overflow-hidden rounded-2xl border border-border/30 bg-card/20" style={{ minHeight: 480 }}>

      {/* Left: DM list */}
      <div className="w-56 shrink-0 border-r border-border/20 flex flex-col bg-card/30">
        <div className="px-4 py-3 border-b border-border/15">
          <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Direct Messages</span>
        </div>

        <div className="flex-1 overflow-y-auto divide-y divide-border/10">
          {DEMO_DMS.map(dm => {
            const active = dm.id === selectedId;
            return (
              <button
                key={dm.id}
                type="button"
                onClick={() => setSelectedId(dm.id)}
                className={`w-full text-left flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/20 ${active ? 'bg-muted/40' : ''}`}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted/60 text-foreground ring-1 ring-border/20 text-[11px] font-bold">
                  {dm.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-xs font-semibold text-foreground truncate">{dm.name}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground/50">{dm.updatedAt}</span>
                  </div>
                  <span className="text-[10px] text-muted-foreground truncate block">{dm.preview}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Right: thread */}
      {selected && (
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-border/20 bg-card/20">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted/60 text-foreground ring-1 ring-border/20 text-[11px] font-bold">
              {selected.initials}
            </span>
            <div>
              <p className="text-sm font-semibold text-foreground leading-none">{selected.name}</p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">{selected.meta}</p>
            </div>
          </div>
          <ChatThread
            messages={selected.messages}
            variant="dm"
            emptyText="No messages yet."
            className="flex-1 min-h-0"
            inputPlaceholder={`Message ${selected.name}…`}
            onSend={() => {}}
          />
        </div>
      )}
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

// Messages tab is a built-in stopgap — DMs only.
// Support tab arrives from GET /api/me/profile-panels via workspace_support/contracts/profile.yaml.
const BUILTIN_TABS = [
  { id: 'messages', label: 'Messages', builtin: true },
];

export default function ProfilePage() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const username = params.username || null;
  const { config, auth, api } = useChatUI();
  const backendUrl = getHostApiBaseUrl(config, api);
  const isOwner = !username;

  // Read ?tab from URL to support deep-linking (e.g. from EscalationCard)
  const urlTabParam = new URLSearchParams(location.search).get('tab') || null;

  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState(null);
  const [tabs, setTabs] = useState([]);
  const [tabsLoading, setTabsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState(urlTabParam || null);

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
    setTabsLoading(true);
    let panelTabs = [];
    let moduleTabs = [];
    if (backendUrl) {
      // profile-panels: older panel contract (title field, workspace_support etc.)
      try {
        const res = await fetchWithAuth(`${backendUrl}/api/me/profile-panels`, {}, auth);
        const body = res.ok ? await res.json() : { panels: [] };
        panelTabs = (Array.isArray(body?.panels) ? body.panels : []).map(p => ({
          id:        p.id,
          label:     p.title,
          component: p.component || null,
          data:      p.data || null,
          order:     p.order ?? 50,
          error:     p.error || null,
        }));
      } catch {
        panelTabs = [];
      }
      // profile-tabs: social/module tab contract (label field, friends/posts/feed etc.)
      try {
        const res = await fetchWithAuth(`${backendUrl}/api/me/profile-tabs`, {}, auth);
        const body = res.ok ? await res.json() : { tabs: [] };
        moduleTabs = (Array.isArray(body?.tabs) ? body.tabs : []).map(t => ({
          id:        t.id,
          label:     t.label,
          component: t.component || null,
          data:      t.data || null,
          order:     t.order ?? 100,
          error:     t.error || null,
        }));
      } catch {
        moduleTabs = [];
      }
    }
    // Merge: builtin first, then panels + module tabs sorted by order
    const remote = [...panelTabs, ...moduleTabs].sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
    const merged = [...BUILTIN_TABS, ...remote];
    setTabs(merged);
    setActiveTab(prev => prev || merged[0]?.id || null);
    setTabsLoading(false);
  }, [backendUrl, auth]);

  useEffect(() => { loadProfile(); }, [loadProfile]);
  useEffect(() => { loadTabs(); }, [loadTabs]);

  if (profileLoading) return <Spinner />;
  if (profileError && !profile) return <ErrorState message={`Could not load profile: ${profileError}`} />;

  const currentTab = tabs.find(t => t.id === activeTab) || tabs[0] || null;

  const handleTabSelect = (id) => {
    setActiveTab(id);
    // Keep URL in sync so deep links work
    const params = new URLSearchParams(location.search);
    params.set('tab', id);
    navigate(`${location.pathname}?${params.toString()}`, { replace: true });
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl pb-16">
        <ProfileHero
          profile={profile}
          isOwner={isOwner}
          backendUrl={backendUrl}
          auth={auth}
          onEdited={loadProfile}
        />

        {!tabsLoading && tabs.length > 0 && (
          <>
            <TabBar tabs={tabs} activeId={currentTab?.id} onSelect={handleTabSelect} />

            {currentTab?.builtin && currentTab.id === 'messages' ? (
              <div className="px-5 sm:px-8 py-6">
                <UserMessagesTab />
              </div>
            ) : (
              <TabContent tab={currentTab} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
