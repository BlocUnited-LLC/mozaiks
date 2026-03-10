/**
 * Admin Portal Registry & Shared Primitives
 *
 * Public API owned by chat-ui — not tied to any platform module.
 * Provides:
 *   - Section registration API  (registerAdminSection, etc.)
 *   - Auth hooks                (useIsAdmin, useHasRole, useAuthConfig)
 *   - Shared UI primitives      (Card, Stat, ProgressBar)
 *
 * Platform modules and app creators import these from '@mozaiks/chat-ui'.
 *
 * @module @mozaiks/chat-ui/adminPortalRegistry
 */

import React, { useMemo } from 'react';
import { useChatUI } from './context/ChatUIContext';

// ---------------------------------------------------------------------------
// Section Registry
// ---------------------------------------------------------------------------

const sectionRegistry = new Map();

/**
 * Register an admin portal section.
 * @param {string} id - Unique section identifier
 * @param {React.ComponentType} component - React component to render
 * @param {object} options
 * @param {string} [options.title]       - Section title
 * @param {number} [options.order]       - Sort order (lower = first)
 * @param {string} [options.requiresRole] - Role required to view (e.g. 'admin')
 * @param {string} [options.category]   - 'user' | 'admin' | 'settings'
 * @param {string} [options.gridSpan]   - 'full' | 'half' | 'third' | 'two-thirds'
 */
export function registerAdminSection(id, component, options = {}) {
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

/** Get a registered section by id. */
export function getAdminSection(id) {
  return sectionRegistry.get(id);
}

/** Get all registered sections sorted by order. */
export function getAllAdminSections() {
  return Array.from(sectionRegistry.values()).sort((a, b) => a.order - b.order);
}

/** Unregister a section. */
export function unregisterAdminSection(id) {
  sectionRegistry.delete(id);
}

// Legacy aliases
export const registerDashboardSection = registerAdminSection;
export const getDashboardSection = getAdminSection;
export const getAllDashboardSections = getAllAdminSections;
export const unregisterDashboardSection = unregisterAdminSection;

// ---------------------------------------------------------------------------
// Auth Hooks
// ---------------------------------------------------------------------------

/**
 * Returns true if the current user has admin access (Keycloak role or adminEmails).
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

/** Returns true if the current user has the given role. */
export function useHasRole(role) {
  const { user } = useChatUI();
  return useMemo(() => user?.roles?.includes(role) || false, [user, role]);
}

/** Returns the auth config object from the auth adapter. */
export function useAuthConfig() {
  const { auth } = useChatUI();
  return auth?.authConfig || {};
}

// ---------------------------------------------------------------------------
// Shared UI Primitives
// ---------------------------------------------------------------------------

/**
 * Card wrapper with consistent styling.
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
 * Stat display component.
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
 * Progress bar component.
 */
export const ProgressBar = ({ percent, warning = false }) => (
  <div className="w-full bg-slate-700 rounded-full h-2">
    <div
      className={`h-2 rounded-full transition-all ${warning ? 'bg-amber-500' : 'bg-cyan-500'}`}
      style={{ width: `${Math.min(percent, 100)}%` }}
    />
  </div>
);
