/**
 * NotificationsDropdown — Bell dropdown panel
 *
 * Self-contained: fetches and manages notification state when open.
 * Rendered directly in the Header next to the bell icon.
 *
 * @module @mozaiks/chat-ui/components/layout/NotificationsDropdown
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  fetchNotifications,
  fetchNotificationCount,
  markNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
} from '../../coreBridge';

// ---------------------------------------------------------------------------
// Inline icons
// ---------------------------------------------------------------------------
const CheckIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
  </svg>
);

const TrashIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
  </svg>
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const typeDots = {
  info: 'bg-cyan-400',
  success: 'bg-green-400',
  warning: 'bg-amber-400',
  error: 'bg-red-400',
};

const typeBorders = {
  info: 'border-cyan-500/30',
  success: 'border-green-500/30',
  warning: 'border-amber-500/30',
  error: 'border-red-500/30',
};

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'warning', label: 'Alerts' },
];

// ---------------------------------------------------------------------------
// NotificationItem
// ---------------------------------------------------------------------------
const NotificationItem = ({ notification, onMarkRead, onDelete }) => {
  const id = notification._id || notification.id;
  const nType = notification.type || 'info';
  const isRead = notification.read;

  return (
    <div className={`flex gap-3 px-4 py-3 border-b border-slate-800/50 last:border-0 ${isRead ? 'opacity-60' : ''}`}>
      {/* type dot */}
      <div className="flex-shrink-0 mt-1.5">
        <div className={`w-2 h-2 rounded-full ${typeDots[nType] || typeDots.info} ${isRead ? 'opacity-30' : ''}`} />
      </div>
      {/* content */}
      <div className="flex-1 min-w-0">
        {notification.title && (
          <p className={`text-sm font-medium truncate ${isRead ? 'text-slate-400' : 'text-white'}`}>
            {notification.title}
          </p>
        )}
        {notification.message && (
          <p className="text-xs text-slate-400 line-clamp-2 mt-0.5">{notification.message}</p>
        )}
        <p className="text-[10px] text-slate-600 mt-1">{timeAgo(notification.created_at)}</p>
      </div>
      {/* actions */}
      <div className="flex-shrink-0 flex items-start gap-1 mt-0.5">
        {!isRead && (
          <button
            onClick={() => onMarkRead(id)}
            title="Mark read"
            className="p-1 rounded text-slate-500 hover:text-cyan-400 hover:bg-slate-700/50 transition-colors"
          >
            <CheckIcon />
          </button>
        )}
        <button
          onClick={() => onDelete(id)}
          title="Delete"
          className="p-1 rounded text-slate-500 hover:text-red-400 hover:bg-slate-700/50 transition-colors"
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// NotificationsDropdown
// ---------------------------------------------------------------------------
const NotificationsDropdown = ({ isOpen, onClose }) => {
  const panelRef = useRef(null);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('all');
  const [unreadCount, setUnreadCount] = useState(0);

  // Fetch on open
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        const [notifs, countRes] = await Promise.all([
          fetchNotifications(40).catch(() => ({ notifications: [] })),
          fetchNotificationCount().catch(() => ({ count: 0 })),
        ]);
        if (cancelled) return;
        setNotifications(notifs?.notifications || notifs || []);
        setUnreadCount(countRes?.count ?? countRes?.unread_count ?? 0);
      } catch {
        // non-fatal
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  const handleMarkRead = useCallback(async (id) => {
    await markNotificationRead(id).catch(() => null);
    setNotifications((prev) => prev.map((n) => ((n._id || n.id) === id ? { ...n, read: true } : n)));
    setUnreadCount((c) => Math.max(0, c - 1));
  }, []);

  const handleMarkAllRead = useCallback(async () => {
    await markAllNotificationsRead().catch(() => null);
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnreadCount(0);
  }, []);

  const handleDelete = useCallback(async (id) => {
    const wasUnread = notifications.find((n) => (n._id || n.id) === id && !n.read);
    await deleteNotification(id).catch(() => null);
    setNotifications((prev) => prev.filter((n) => (n._id || n.id) !== id));
    if (wasUnread) setUnreadCount((c) => Math.max(0, c - 1));
  }, [notifications]);

  const filtered = useMemo(() => {
    if (filter === 'unread') return notifications.filter((n) => !n.read);
    if (filter === 'warning') return notifications.filter((n) => ['warning', 'error'].includes(n.type));
    return notifications;
  }, [notifications, filter]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop (mobile) */}
      <div
        className="md:hidden fixed inset-0 bg-black/50 z-[60]"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={`
          fixed md:absolute z-[70]
          inset-x-0 bottom-0 md:inset-auto md:right-0 md:top-full md:mt-2
          w-full md:w-80
          rounded-t-2xl md:rounded-2xl
          border border-[rgba(var(--color-primary-light-rgb),0.25)]
          bg-[rgba(5,10,24,0.97)] backdrop-blur-xl
          shadow-[0_-10px_40px_rgba(2,6,23,0.8)] md:shadow-[0_20px_60px_rgba(2,6,23,0.6)]
          overflow-hidden
          max-h-[70vh] md:max-h-[420px]
          flex flex-col
        `}
      >
        {/* Drag handle (mobile) */}
        <div className="md:hidden flex justify-center pt-3 pb-1 flex-shrink-0">
          <div className="w-10 h-1 rounded-full bg-white/20" />
        </div>

        {/* Header row */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(var(--color-primary-light-rgb),0.15)] flex-shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-[rgba(var(--color-primary-light-rgb),0.7)] uppercase tracking-widest oxanium">
              Notifications
            </span>
            {unreadCount > 0 && (
              <span className="text-[10px] font-bold bg-[var(--color-error)] text-white px-1.5 py-0.5 rounded-full oxanium">
                {unreadCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllRead}
                className="text-[10px] text-[rgba(var(--color-primary-light-rgb),0.6)] hover:text-[var(--color-primary-light)] transition-colors oxanium"
              >
                Mark all read
              </button>
            )}
            <button
              onClick={onClose}
              className="md:hidden w-6 h-6 flex items-center justify-center rounded-full hover:bg-white/10 text-slate-400"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1.5 px-4 py-2 border-b border-[rgba(var(--color-primary-light-rgb),0.08)] flex-shrink-0">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors oxanium ${
                filter === f.key
                  ? 'bg-[rgba(var(--color-primary-rgb),0.2)] text-[var(--color-primary-light)] border border-[rgba(var(--color-primary-rgb),0.3)]'
                  : 'text-slate-500 hover:text-slate-300 border border-transparent'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Notification list */}
        <div className="overflow-y-auto flex-1">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-[var(--color-primary)]" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-slate-500">
              <svg className="w-8 h-8 mb-2 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
              </svg>
              <p className="text-xs oxanium">All caught up</p>
            </div>
          ) : (
            filtered.map((n) => (
              <NotificationItem
                key={n._id || n.id}
                notification={n}
                onMarkRead={handleMarkRead}
                onDelete={handleDelete}
              />
            ))
          )}
        </div>

        {/* Safe area spacer (mobile) */}
        <div className="md:hidden h-6 flex-shrink-0" />
      </div>
    </>
  );
};

export default NotificationsDropdown;
