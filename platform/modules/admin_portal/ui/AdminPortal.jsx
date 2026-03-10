/**
 * AdminPortal — Modular admin portal and user account management
 *
 * Platform module UI for admin_portal.
 * Rendered at /admin — registered via @modules auto-discovery (not a core route).
 *
 * Every app built on mozaiks gets this module out of the box.
 * Add custom sections via registerAdminSection() from '@mozaiks/chat-ui'.
 *
 * @module platform/modules/admin_portal/ui/AdminPortal
 */

import React, { useState, useEffect, useMemo } from 'react';
import {
  useChatUI,
  Header,
  Footer,
  registerAdminSection,
  getAllAdminSections,
  useIsAdmin,
  useAuthConfig,
  Card,
  Stat,
  ProgressBar,
} from '@mozaiks/chat-ui';
import { adminListUsers, adminGetAnalytics } from '@mozaiks/chat-ui/coreBridge';

// ---------------------------------------------------------------------------
// Built-in Sections
// ---------------------------------------------------------------------------

const ProfileSection = ({ user }) => {
  if (!user) return null;

  return (
    <Card title="Profile">
      <div className="flex items-start gap-4">
        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-white text-2xl font-bold shrink-0">
          {(user.name?.[0] || user.email?.[0] || '?').toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="text-white font-medium text-lg truncate">{user.name || 'User'}</h4>
          <p className="text-slate-400 text-sm truncate">{user.email}</p>
          <div className="flex flex-wrap gap-2 mt-2">
            {user.roles?.map(role => (
              <span
                key={role}
                className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  role === 'admin'
                    ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                    : 'bg-slate-600/50 text-slate-300'
                }`}
              >
                {role}
              </span>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
};

const UsageSection = ({ user, api }) => {
  const [stats, setStats] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;

    const fetchUsage = async () => {
      try {
        if (api?.getUsage) {
          const data = await api.getUsage(user?.user_id);
          if (!cancelled) setStats({ loading: false, error: null, data });
        } else {
          await new Promise(r => setTimeout(r, 300));
          if (!cancelled) {
            setStats({
              loading: false,
              error: null,
              data: { tokensUsed: 0, totalCost: 0, conversationCount: 0 },
            });
          }
        }
      } catch (err) {
        if (!cancelled) setStats({ loading: false, error: err.message, data: null });
      }
    };

    fetchUsage();
    return () => { cancelled = true; };
  }, [user?.user_id, api]);

  if (stats.loading) {
    return (
      <Card title="Usage This Month">
        <div className="animate-pulse space-y-3">
          <div className="h-8 bg-slate-700 rounded w-1/2"></div>
          <div className="h-4 bg-slate-700 rounded w-3/4"></div>
        </div>
      </Card>
    );
  }

  const data = stats.data || {};

  return (
    <Card title="Usage This Month">
      <div className="grid grid-cols-3 gap-4">
        <Stat value={(data.tokensUsed || 0).toLocaleString()} label="Tokens" color="cyan" />
        <Stat value={`$${(data.totalCost || 0).toFixed(2)}`} label="Cost" color="green" />
        <Stat value={data.conversationCount || 0} label="Conversations" color="purple" />
      </div>
    </Card>
  );
};

const BudgetSection = ({ user, api }) => {
  const [budget, setBudget] = useState({ loading: true, data: null });

  useEffect(() => {
    let cancelled = false;

    const fetchBudget = async () => {
      try {
        if (api?.getBudget) {
          const data = await api.getBudget(user?.user_id);
          if (!cancelled) setBudget({ loading: false, data });
        } else {
          await new Promise(r => setTimeout(r, 200));
          if (!cancelled) {
            setBudget({
              loading: false,
              data: { limit: 10.00, spent: 0, period: 'monthly' },
            });
          }
        }
      } catch {
        if (!cancelled) setBudget({ loading: false, data: { limit: 0, spent: 0 } });
      }
    };

    fetchBudget();
    return () => { cancelled = true; };
  }, [user?.user_id, api]);

  if (budget.loading) {
    return (
      <Card title="Budget">
        <div className="animate-pulse h-16 bg-slate-700 rounded"></div>
      </Card>
    );
  }

  const data = budget.data || { limit: 0, spent: 0 };
  const percentUsed = data.limit > 0 ? (data.spent / data.limit) * 100 : 0;
  const isWarning = percentUsed >= 80;

  return (
    <Card title="Budget">
      <div className="space-y-3">
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">Monthly limit</span>
          <span className="text-white">${data.limit.toFixed(2)}</span>
        </div>
        <ProgressBar percent={percentUsed} warning={isWarning} />
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">${data.spent.toFixed(2)} used</span>
          <span className={isWarning ? 'text-amber-400' : 'text-slate-400'}>
            {percentUsed.toFixed(1)}%
          </span>
        </div>
      </div>
    </Card>
  );
};

const ActionsSection = ({ onNavigate, onLogout }) => (
  <Card title="Quick Actions">
    <div className="space-y-2">
      <button
        onClick={() => onNavigate?.('/')}
        className="w-full text-left px-4 py-3 rounded-xl bg-slate-700/30 hover:bg-slate-700/50 transition-colors text-white flex items-center gap-3"
      >
        <span className="text-slate-400">💬</span>
        Back to Chat
      </button>
      <button
        onClick={onLogout}
        className="w-full text-left px-4 py-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 transition-colors text-red-400 flex items-center gap-3"
      >
        <span>🚪</span>
        Sign Out
      </button>
    </div>
  </Card>
);

const AdminOverviewSection = () => {
  const authConfig = useAuthConfig();
  const adminEmails = authConfig.roles?.adminEmails || [];

  const [users, setUsers] = useState({ loading: true, total: '--', error: null });
  const [analytics, setAnalytics] = useState({ loading: true, tokens: '--', error: null });

  useEffect(() => {
    let cancelled = false;

    const loadAdminData = async () => {
      try {
        const res = await adminListUsers(1, 1);
        if (!cancelled) setUsers({ loading: false, total: res?.total ?? '--', error: null });
      } catch {
        if (!cancelled) setUsers({ loading: false, total: '--', error: 'unavailable' });
      }

      try {
        const res = await adminGetAnalytics();
        if (!cancelled) {
          const totalTokens = res?.kpis?.total_tokens ?? res?.total_tokens ?? '--';
          setAnalytics({ loading: false, tokens: totalTokens, error: null });
        }
      } catch {
        if (!cancelled) setAnalytics({ loading: false, tokens: '--', error: 'unavailable' });
      }
    };

    loadAdminData();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white flex items-center gap-2">
        <span className="w-2 h-2 bg-amber-500 rounded-full"></span>
        Admin Portal
      </h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card title="App Users" subtitle="Manage users and permissions">
          {users.loading ? (
            <div className="animate-pulse h-8 bg-slate-700 rounded w-1/3"></div>
          ) : (
            <Stat
              value={typeof users.total === 'number' ? users.total.toLocaleString() : users.total}
              label="Total registered users"
              color="white"
            />
          )}
        </Card>

        <Card title="Platform Usage" subtitle="Total usage this month">
          {analytics.loading ? (
            <div className="animate-pulse h-8 bg-slate-700 rounded w-1/3"></div>
          ) : (
            <Stat
              value={typeof analytics.tokens === 'number' ? analytics.tokens.toLocaleString() : analytics.tokens}
              label="Total tokens consumed"
              color="white"
            />
          )}
        </Card>
      </div>

      <Card title="Admin Configuration">
        <p className="text-slate-400 text-sm mb-3">
          Users with admin access (via app.json):
        </p>
        <div className="flex flex-wrap gap-2">
          {adminEmails.length > 0 ? (
            adminEmails.map(email => (
              <span key={email} className="px-3 py-1 bg-slate-700/50 rounded-lg text-sm text-slate-300">
                {email}
              </span>
            ))
          ) : (
            <span className="text-slate-500 text-sm">No admin emails configured</span>
          )}
        </div>
        <p className="text-slate-500 text-xs mt-3">
          Edit <code className="text-cyan-400">platform/app.json</code> → auth.roles.adminEmails
        </p>
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Register Built-in Sections (side-effect on first import)
// ---------------------------------------------------------------------------

registerAdminSection('profile', ProfileSection, {
  title: 'Profile',
  order: 10,
  category: 'user',
  gridSpan: 'two-thirds',
});

registerAdminSection('actions', ActionsSection, {
  title: 'Quick Actions',
  order: 15,
  category: 'user',
  gridSpan: 'third',
});

registerAdminSection('usage', UsageSection, {
  title: 'Usage',
  order: 20,
  category: 'user',
  gridSpan: 'half',
});

registerAdminSection('budget', BudgetSection, {
  title: 'Budget',
  order: 25,
  category: 'user',
  gridSpan: 'half',
});

registerAdminSection('admin-overview', AdminOverviewSection, {
  title: 'Admin Overview',
  order: 100,
  category: 'admin',
  requiresRole: 'admin',
  gridSpan: 'full',
});

// ---------------------------------------------------------------------------
// Main AdminPortal Component
// ---------------------------------------------------------------------------

const AdminPortal = () => {
  const { user, api, logout, loading } = useChatUI();
  const isAdmin = useIsAdmin();

  const sections = useMemo(() => {
    return getAllAdminSections().filter(section => {
      if (section.requiresRole === 'admin' && !isAdmin) return false;
      return true;
    });
  }, [isAdmin]);

  const userSections = sections.filter(s => s.category === 'user');
  const adminSections = sections.filter(s => s.category === 'admin');

  const handleNavigate = (path) => {
    window.location.href = path;
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500"></div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
        <Header />
        <div className="pt-20 px-4 max-w-4xl mx-auto">
          <Card>
            <div className="text-center py-8">
              <p className="text-slate-400 mb-4">Please sign in to access the admin portal</p>
              <button
                onClick={() => handleNavigate('/')}
                className="px-6 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-white transition-colors"
              >
                Go to Login
              </button>
            </div>
          </Card>
        </div>
        <Footer />
      </div>
    );
  }

  const renderSection = (section) => {
    const Component = section.component;
    return (
      <Component
        key={section.id}
        user={user}
        api={api}
        isAdmin={isAdmin}
        onNavigate={handleNavigate}
        onLogout={logout}
      />
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      <Header user={user} />

      <main className="pt-20 pb-12 px-4">
        <div className="max-w-4xl mx-auto space-y-8">
          <div>
            <h1 className="text-2xl font-bold text-white">Admin Portal</h1>
            <p className="text-slate-400 mt-1">Manage your account, view usage, and access admin features</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {userSections.map(section => {
              const spanClass = {
                'full': 'md:col-span-3',
                'two-thirds': 'md:col-span-2',
                'half': 'md:col-span-3 lg:col-span-1',
                'third': 'md:col-span-1',
              }[section.gridSpan] || '';

              return (
                <div key={section.id} className={spanClass}>
                  {renderSection(section)}
                </div>
              );
            })}
          </div>

          {adminSections.length > 0 && (
            <div className="pt-6 border-t border-slate-700/50">
              {adminSections.map(renderSection)}
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  );
};

export default AdminPortal;
