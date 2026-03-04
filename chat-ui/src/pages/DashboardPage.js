/**
 * DashboardPage — Modular user dashboard and admin portal
 *
 * ARCHITECTURE:
 * - Config-driven: Sections defined in ui.json → dashboard.sections
 * - Extensible: Register custom sections via registerDashboardSection()
 * - Role-based: Sections can require specific roles
 * - Themeable: Uses existing theme system
 *
 * USAGE:
 * 1. Default sections are registered automatically (profile, usage, budget, admin)
 * 2. Add custom sections: registerDashboardSection('my-section', MyComponent, { order: 50 })
 * 3. Configure in ui.json: dashboard.sections = ['profile', 'usage', 'my-section']
 *
 * This is the foundation for all apps in the agentic future - every app needs
 * a dashboard for users to manage their account, view usage, and access admin features.
 *
 * @module @mozaiks/chat-ui/pages/DashboardPage
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useChatUI } from '../context/ChatUIContext';
import Header from '../components/layout/Header';
import Footer from '../components/layout/Footer';

// ---------------------------------------------------------------------------
// Section Registry — allows app creators to register custom dashboard sections
// ---------------------------------------------------------------------------

const sectionRegistry = new Map();

/**
 * Register a dashboard section
 * @param {string} id - Unique section identifier
 * @param {React.ComponentType} component - React component to render
 * @param {object} options
 * @param {string} [options.title] - Section title
 * @param {number} [options.order] - Sort order (lower = first)
 * @param {string} [options.requiresRole] - Role required to view (e.g., 'admin')
 * @param {string} [options.category] - 'user' | 'admin' | 'settings'
 * @param {string} [options.gridSpan] - 'full' | 'half' | 'third'
 */
export function registerDashboardSection(id, component, options = {}) {
  sectionRegistry.set(id, {
    id,
    component,
    title: options.title || id,
    order: options.order ?? 100,
    requiresRole: options.requiresRole || null,
    category: options.category || 'user',
    gridSpan: options.gridSpan || 'half',
  });
}

/**
 * Get a registered section
 */
export function getDashboardSection(id) {
  return sectionRegistry.get(id);
}

/**
 * Get all registered sections (sorted by order)
 */
export function getAllDashboardSections() {
  return Array.from(sectionRegistry.values()).sort((a, b) => a.order - b.order);
}

/**
 * Unregister a section
 */
export function unregisterDashboardSection(id) {
  sectionRegistry.delete(id);
}

// ---------------------------------------------------------------------------
// Auth Hooks
// ---------------------------------------------------------------------------

/**
 * Check if user has admin access (Keycloak role OR adminEmails config)
 */
export function useIsAdmin() {
  const { user, auth } = useChatUI();

  return useMemo(() => {
    if (!user) return false;

    const authConfig = auth?.authConfig || {};
    const rolesConfig = authConfig.roles || {};
    const adminRole = rolesConfig.admin || 'admin';
    const adminEmails = rolesConfig.adminEmails || [];

    const hasAdminRole = user.roles?.includes(adminRole);
    const hasAdminEmail = adminEmails.includes(user.email);

    return hasAdminRole || hasAdminEmail;
  }, [user, auth]);
}

/**
 * Check if user has a specific role
 */
export function useHasRole(role) {
  const { user } = useChatUI();
  return useMemo(() => user?.roles?.includes(role) || false, [user, role]);
}

/**
 * Get auth config from adapter
 */
export function useAuthConfig() {
  const { auth } = useChatUI();
  return auth?.authConfig || {};
}

// ---------------------------------------------------------------------------
// Shared UI Components
// ---------------------------------------------------------------------------

/**
 * Card wrapper with consistent styling
 */
export const Card = ({ title, subtitle, children, className = '', actions }) => (
  <div className={`bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 ${className}`}>
    {(title || actions) && (
      <div className="flex items-start justify-between mb-4">
        <div>
          {title && (
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              {title}
            </h3>
          )}
          {subtitle && <p className="text-slate-400 text-sm mt-1">{subtitle}</p>}
        </div>
        {actions && <div className="flex gap-2">{actions}</div>}
      </div>
    )}
    {children}
  </div>
);

/**
 * Stat display component
 */
export const Stat = ({ value, label, color = 'cyan' }) => {
  const colorClasses = {
    cyan: 'text-cyan-400',
    green: 'text-green-400',
    purple: 'text-purple-400',
    amber: 'text-amber-400',
    red: 'text-red-400',
    white: 'text-white',
  };

  return (
    <div>
      <p className={`text-2xl font-bold ${colorClasses[color] || colorClasses.cyan}`}>
        {value}
      </p>
      <p className="text-slate-400 text-sm">{label}</p>
    </div>
  );
};

/**
 * Progress bar component
 */
export const ProgressBar = ({ percent, warning = false }) => (
  <div className="w-full bg-slate-700 rounded-full h-2">
    <div
      className={`h-2 rounded-full transition-all ${warning ? 'bg-amber-500' : 'bg-cyan-500'}`}
      style={{ width: `${Math.min(percent, 100)}%` }}
    />
  </div>
);

// ---------------------------------------------------------------------------
// Built-in Sections
// ---------------------------------------------------------------------------

/**
 * Profile info section
 */
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

/**
 * Usage stats section
 */
const UsageSection = ({ user, api }) => {
  const [stats, setStats] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;

    const fetchUsage = async () => {
      try {
        // Try API first, fall back to placeholder
        if (api?.getUsage) {
          const data = await api.getUsage(user?.user_id);
          if (!cancelled) setStats({ loading: false, error: null, data });
        } else {
          // Placeholder data
          await new Promise(r => setTimeout(r, 300));
          if (!cancelled) {
            setStats({
              loading: false,
              error: null,
              data: { tokensUsed: 0, totalCost: 0, conversationCount: 0 }
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

/**
 * Budget status section
 */
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
          // Placeholder
          await new Promise(r => setTimeout(r, 200));
          if (!cancelled) {
            setBudget({
              loading: false,
              data: { limit: 10.00, spent: 0, period: 'monthly' }
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

/**
 * Quick actions section
 */
const ActionsSection = ({ onNavigate, onLogout }) => (
  <Card title="Quick Actions">
    <div className="space-y-2">
      <button
        onClick={() => onNavigate?.('/settings')}
        className="w-full text-left px-4 py-3 rounded-xl bg-slate-700/30 hover:bg-slate-700/50 transition-colors text-white flex items-center gap-3"
      >
        <span className="text-slate-400">⚙️</span>
        Settings
      </button>
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

/**
 * Admin overview section
 */
const AdminOverviewSection = () => {
  const authConfig = useAuthConfig();
  const adminEmails = authConfig.roles?.adminEmails || [];

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white flex items-center gap-2">
        <span className="w-2 h-2 bg-amber-500 rounded-full"></span>
        Admin Portal
      </h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card title="App Users" subtitle="Manage users and permissions">
          <Stat value="--" label="Total registered users" color="white" />
        </Card>

        <Card title="Platform Usage" subtitle="Total usage this month">
          <Stat value="--" label="Total tokens consumed" color="white" />
        </Card>
      </div>

      <Card title="Admin Configuration">
        <p className="text-slate-400 text-sm mb-3">
          Users with admin access (via auth.json):
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
          Edit <code className="text-cyan-400">app/brand/public/auth.json</code> → roles.adminEmails
        </p>
      </Card>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Register Built-in Sections
// ---------------------------------------------------------------------------

// User sections
registerDashboardSection('profile', ProfileSection, {
  title: 'Profile',
  order: 10,
  category: 'user',
  gridSpan: 'two-thirds',
});

registerDashboardSection('actions', ActionsSection, {
  title: 'Quick Actions',
  order: 15,
  category: 'user',
  gridSpan: 'third',
});

registerDashboardSection('usage', UsageSection, {
  title: 'Usage',
  order: 20,
  category: 'user',
  gridSpan: 'half',
});

registerDashboardSection('budget', BudgetSection, {
  title: 'Budget',
  order: 25,
  category: 'user',
  gridSpan: 'half',
});

// Admin sections
registerDashboardSection('admin-overview', AdminOverviewSection, {
  title: 'Admin Overview',
  order: 100,
  category: 'admin',
  requiresRole: 'admin',
  gridSpan: 'full',
});

// ---------------------------------------------------------------------------
// Main DashboardPage Component
// ---------------------------------------------------------------------------

const DashboardPage = () => {
  const { user, api, logout, loading } = useChatUI();
  const isAdmin = useIsAdmin();

  // Get sections to render (could be config-driven from ui.json in future)
  const sections = useMemo(() => {
    const allSections = getAllDashboardSections();

    return allSections.filter(section => {
      // Check role requirement
      if (section.requiresRole === 'admin' && !isAdmin) {
        return false;
      }
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
              <p className="text-slate-400 mb-4">Please sign in to view your dashboard</p>
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

  // Render a section
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
          {/* Page header */}
          <div>
            <h1 className="text-2xl font-bold text-white">Dashboard</h1>
            <p className="text-slate-400 mt-1">Manage your account, view usage, and access admin features</p>
          </div>

          {/* User sections - responsive grid */}
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

          {/* Admin sections */}
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

export default DashboardPage;
