import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { DEFAULT_HEADER_CONFIG } from "../../styles/themeProvider";
import { useNavigation } from "../../providers/NavigationProvider";
import { useNavigationActions } from "../../navigation/useNavigationActions";
import {
  deriveShellActionContext,
  getNavigationTargetKey,
  getUserRoles,
  resolveShellAction,
  resolveShellActions,
} from "../../navigation/shellActions";
import { useChatUI } from "../../context/ChatUIContext";
import { useAppEventBus } from "../../ui/hooks/useAppEventBus.js";
import "./header-styles.css";

const ICON_FILE_RE = /\.(svg|png|jpe?g|gif|webp|ico)$/i;

const resolveAssetSource = (value) => {
  if (!value || typeof value !== "string") return null;
  if (value.startsWith("/") || value.startsWith("http")) return value;
  if (ICON_FILE_RE.test(value)) return `/assets/${value}`;
  return null;
};

const isInternalHref = (value) => typeof value === "string" && value.startsWith("/");

const getUserLabel = (user, fallback) => {
  if (!user) return fallback;
  return user.firstName || user.name || user.username || user.email || user.id || fallback;
};

const getUserSubLabel = (user, fallback) => {
  if (!user) return fallback;
  return user.email || user.title || fallback;
};

const isMenuItemVisible = (item, roles) => {
  if (!item || item.visible === false) return false;
  if (!item.requiresRole) return true;
  return roles.includes(item.requiresRole);
};

const isAuthenticatedUser = (user) => {
  const id = String(user?.id || user?.user_id || "").toLowerCase();
  return Boolean(user) && id !== "anonymous" && id !== "guest";
};

const getDefaultProfileMenu = (user) => {
  const authed = isAuthenticatedUser(user);
  const items = [
    {
      id: "profile",
      label: "Account",
      action: "navigate",
      href: "/profile",
    },
  ];

  if (authed) {
    items.push({
      id: "signout",
      label: "Sign Out",
      icon: "logout.svg",
      action: "signout",
      variant: "danger",
    });
  } else {
    items.push({
      id: "signin",
      label: "Sign In",
      action: "signin",
    });
  }

  return items;
};

const mergeProfileMenu = (defaultItems, configuredItems = []) => {
  if (!Array.isArray(configuredItems) || configuredItems.length === 0) {
    return defaultItems;
  }

  const configuredById = new Map(
    configuredItems
      .filter((item) => item && item.id && item.type !== "divider")
      .map((item) => [item.id, item]),
  );
  const defaultIds = new Set(defaultItems.map((item) => item.id));
  const merged = defaultItems.map((item) => ({
    ...item,
    ...(configuredById.get(item.id) || {}),
  }));

  for (const item of configuredItems) {
    if (!item || item.type === "divider" || defaultIds.has(item.id)) continue;
    merged.splice(Math.max(merged.length - 1, 0), 0, item);
  }

  return merged;
};

const DefaultBellIcon = ({ className = "h-5 w-5" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17H9.143m5.714 0a2.857 2.857 0 1 1-5.714 0m5.714 0H18l-1.714-2.143V10.57a4.286 4.286 0 1 0-8.572 0v4.286L6 17h8.857Z" />
  </svg>
);

const DefaultPrimaryActionIcon = ({ className = "h-5 w-5" }) => (
  <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.25">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2m0 14v2m9-9h-2M5 12H3m15.364-6.364-1.414 1.414M6.343 17.657l-1.414 1.414m12.728 0-1.414-1.414M6.343 6.343 4.929 4.929" />
    <circle cx="12" cy="12" r="5" stroke="currentColor" strokeWidth="2.25" />
  </svg>
);

const ChevronIcon = ({ open = false }) => (
  <svg
    className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
  >
    <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
  </svg>
);

const ActionIcon = ({ icon, className = "h-4 w-4" }) => {
  const src = resolveAssetSource(icon);
  if (!src) return null;
  return (
    <span
      aria-hidden="true"
      className={`inline-block ${className}`}
      style={{
        backgroundColor: "currentColor",
        maskImage: `url(${src})`,
        WebkitMaskImage: `url(${src})`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  );
};

const Header = ({
  user = null,
  chatTheme = null,
  route = null,
  shellMode = null,
  onAction = () => {},
  onNotificationClick = null,
}) => {
  const location = useLocation();
  const {
    headerPages,
    header: navHeader,
    profile: navProfile,
    notifications: navNotifications,
  } = useNavigation();
  const handleNavigationItem = useNavigationActions();
  const { login, logout } = useChatUI();

  const headerConfig = {
    ...DEFAULT_HEADER_CONFIG,
    ...navHeader,
  };
  const logoConfig = { ...DEFAULT_HEADER_CONFIG.logo, ...headerConfig?.logo };
  const notificationsConfig = navNotifications || {};
  const profileConfig = navProfile || {};
  const brandConfig = { name: "MozaiksAI", ...(chatTheme?.branding || {}) };

  const currentUser = user || { id: "anonymous", firstName: "Guest", userPhoto: null };
  const userRoles = useMemo(() => getUserRoles(currentUser), [currentUser]);
  const shellActionContext = useMemo(
    () => deriveShellActionContext({ location, route, shellMode, user: currentUser, roles: userRoles }),
    [currentUser, location.pathname, location.search, route, shellMode, userRoles]
  );
  const headerActions = useMemo(
    () => (Array.isArray(headerConfig.actions)
      ? resolveShellActions(headerConfig.actions, shellActionContext)
      : []),
    [headerConfig.actions, shellActionContext]
  );
  const visibleHeaderPages = useMemo(() => {
    const actionTargets = new Set(
      headerActions
        .map((item) => getNavigationTargetKey(item))
        .filter(Boolean)
    );

    return headerPages.filter((item) => {
      const target = getNavigationTargetKey(item);
      return !target || !actionTargets.has(target);
    });
  }, [headerActions, headerPages, userRoles]);
  const primaryAction = useMemo(
    () => headerActions.find((item) => item?.variant === "gradient") || headerActions[0] || null,
    [headerActions]
  );
  const profileMenu = useMemo(
    () => mergeProfileMenu(getDefaultProfileMenu(currentUser), profileConfig.menu)
      .filter((item) => isMenuItemVisible(item, userRoles)),
    [currentUser, profileConfig.menu, userRoles]
  );

  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  const [notificationCount, setNotificationCount] = useState(0);

  const profileRef = useRef(null);
  const notificationsRef = useRef(null);

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 12);
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const handlePointerDown = (event) => {
      if (profileRef.current && !profileRef.current.contains(event.target)) {
        setIsProfileOpen(false);
      }
      if (notificationsRef.current && !notificationsRef.current.contains(event.target)) {
        setIsNotificationsOpen(false);
      }
    };

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setIsProfileOpen(false);
        setIsNotificationsOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown, { passive: true });
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  useEffect(() => {
    if (notificationsConfig.show === false) return undefined;

    const controller = new AbortController();
    let mounted = true;

    const loadNotificationCount = async () => {
      try {
        const response = await fetch("/api/notifications/count", {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const payload = await response.json();
        const count = Number(payload?.unread_count ?? payload?.count ?? 0);
        if (mounted && Number.isFinite(count)) {
          setNotificationCount(Math.max(0, count));
        }
      } catch (_) {
        if (mounted) {
          setNotificationCount(0);
        }
      }
    };

    loadNotificationCount();
    return () => {
      mounted = false;
      controller.abort();
    };
  }, [notificationsConfig.show]);

  useAppEventBus('notification.count_changed', () => {
    if (notificationsConfig.show === false) return;
    fetch('/api/notifications/count', { headers: { Accept: 'application/json' } })
      .then((r) => r.ok ? r.json() : null)
      .then((payload) => {
        if (!payload) return;
        const count = Number(payload?.unread_count ?? payload?.count ?? 0);
        if (Number.isFinite(count)) setNotificationCount(Math.max(0, count));
      })
      .catch(() => {});
  });

  const handleClearAllNotifications = async () => {
    try {
      await fetch('/api/notifications', { method: 'DELETE', headers: { Accept: 'application/json' } });
      setNotificationCount(0);
      setIsNotificationsOpen(false);
    } catch (_) {}
  };

  const executeAction = async (action) => {
    if (!action) return;
    const resolvedAction = resolveShellAction(action, shellActionContext);

    if (resolvedAction.action === "signout" || resolvedAction.id === "signout") {
      await logout?.();
      setIsProfileOpen(false);
      return;
    }

    if (resolvedAction.action === "signin" || resolvedAction.id === "signin") {
      await login?.();
      setIsProfileOpen(false);
      return;
    }

    if (resolvedAction.path || resolvedAction.href || resolvedAction.trigger || resolvedAction.action === "navigate") {
      handleNavigationItem(resolvedAction);
      setIsProfileOpen(false);
      setIsNotificationsOpen(false);
      return;
    }

    if (resolvedAction.id) {
      onAction(resolvedAction.id, resolvedAction);
    }
  };

  const brandName = brandConfig.name || "MozaiksAI";
  const logoSrc = resolveAssetSource(logoConfig.src) || brandConfig.logo || null;
  const wordmarkSrc = resolveAssetSource(logoConfig.wordmark) || brandConfig.wordmark || null;
  const profileIconSrc = resolveAssetSource(profileConfig.icon);
  const notificationsIconSrc = resolveAssetSource(notificationsConfig.icon);
  const profileLabel = getUserLabel(currentUser, profileConfig.defaultLabel || "User");
  const profileSubLabel = getUserSubLabel(currentUser, profileConfig.sublabel || "");
  const showProfile = profileConfig.show !== false;
  const showNotifications = notificationsConfig.show !== false;
  const notificationsPath = notificationsConfig.path;
  const primaryActionLabel = primaryAction?.label || primaryAction?.id || "Action";
  const headerFrameStyle = {
    minHeight: "var(--shell-header-height, 4rem)",
    gap: "var(--shell-header-gap, 1rem)",
    paddingInline: 0,
  };
  const headerClusterStyle = {
    gap: "var(--shell-header-cluster-gap, 0.875rem)",
  };
  const headerNavStyle = {
    gap: "var(--shell-header-nav-gap, 0.5rem)",
    paddingLeft: "var(--shell-header-nav-padding-left, 0.5rem)",
  };
  const headerRightStyle = {
    gap: "var(--shell-header-control-gap, 0.75rem)",
  };
  const navButtonStyle = {
    minHeight: "calc(var(--shell-header-action-height, 2.75rem) - 0.75rem)",
    paddingInline: "calc(var(--shell-header-action-padding-x, 1rem) * 0.75)",
    borderRadius: "var(--shell-control-radius, 0.75rem)",
  };
  const primaryActionStyle = {
    minHeight: "var(--shell-header-action-height, 2.75rem)",
    paddingInline: "var(--shell-header-action-padding-x, 1rem)",
    borderRadius: "var(--shell-control-radius, 0.875rem)",
  };
  const mobilePrimaryActionStyle = {
    width: "var(--shell-header-utility-size, 2.75rem)",
    height: "var(--shell-header-utility-size, 2.75rem)",
    borderRadius: "calc(var(--shell-control-radius, 1rem) + 0.25rem)",
  };
  const utilityButtonStyle = {
    width: "var(--shell-header-utility-size, 2.75rem)",
    height: "var(--shell-header-utility-size, 2.75rem)",
    padding: "var(--shell-header-utility-padding, 0.375rem)",
    borderRadius: "var(--shell-control-radius, 0.875rem)",
  };
  const profileButtonStyle = {
    gap: "calc(var(--shell-header-control-gap, 0.75rem) * 0.75)",
    minHeight: "var(--shell-header-profile-height, 2.875rem)",
    paddingInline: "var(--shell-header-profile-padding-x, 0.75rem)",
    borderRadius: "var(--shell-control-radius, 0.875rem)",
  };
  const avatarStyle = {
    width: "var(--shell-header-avatar-size, 2rem)",
    height: "var(--shell-header-avatar-size, 2rem)",
  };
  const avatarLargeStyle = {
    width: "var(--shell-header-avatar-large-size, 3rem)",
    height: "var(--shell-header-avatar-large-size, 3rem)",
  };
  const dropdownPanelStyle = {
    borderRadius: "var(--shell-header-panel-radius, 1rem)",
  };

  const renderPrimaryActionIcon = (className = "h-5 w-5") => (
    primaryAction?.icon ? <ActionIcon icon={primaryAction.icon} className={className} /> : <DefaultPrimaryActionIcon className={className} />
  );

  const LogoSection = () => {
    const content = (
      <span className="flex items-center min-w-0" style={{ gap: "calc(var(--shell-header-cluster-gap, 0.875rem) * 0.7)" }}>
        {logoSrc && (
          <img
            src={logoSrc}
            className="h-7 w-7 object-contain"
            alt={logoConfig.alt || brandName || "Logo"}
          />
        )}
        <span className="min-w-0">
          {wordmarkSrc ? (
            <img
              src={wordmarkSrc}
              className="h-6 max-w-[10.5rem] object-contain opacity-90 sm:h-7 sm:max-w-[12rem]"
              alt={brandName || "Brand"}
            />
          ) : (
            <span className="block truncate text-sm font-semibold uppercase tracking-[0.18em] text-white md:text-base heading-font">
              {brandName}
            </span>
          )}
        </span>
      </span>
    );

    if (logoConfig.href) {
      if (isInternalHref(logoConfig.href)) {
        return (
          <button
            type="button"
            onClick={() => handleNavigationItem({ path: logoConfig.href })}
            className="appearance-none border-0 bg-transparent p-0"
          >
            {content}
          </button>
        );
      }
      return (
        <a
          href={logoConfig.href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-w-0 items-center"
        >
          {content}
        </a>
      );
    }

    return <div className="inline-flex min-w-0 items-center">{content}</div>;
  };

  return (
    <header className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${isScrolled ? "backdrop-blur-md bg-black/25" : "backdrop-blur-md bg-black/15"} border-b border-[rgba(var(--color-primary-rgb),0.1)]`}>
      <div className="relative flex w-full items-center justify-between" style={headerFrameStyle}>
        <div className="flex min-w-0 items-center" style={headerClusterStyle}>
          <LogoSection />

          {visibleHeaderPages.length > 0 && (
            <nav className="hidden xl:flex items-center" style={headerNavStyle}>
              {visibleHeaderPages.map((item) => {
                const isActive = location.pathname === item.path;
                return (
                  <button
                    key={item.id || item.path || item.label}
                    type="button"
                    onClick={() => handleNavigationItem(item)}
                    className={`rounded-lg border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] transition heading-font ${isActive
                      ? "border-[rgba(var(--color-primary-light-rgb),0.55)] bg-[rgba(var(--color-primary-rgb),0.15)] text-white"
                      : "border-[rgba(148,163,184,0.18)] bg-[rgba(255,255,255,0.03)] text-[rgba(226,232,240,0.8)] hover:border-[rgba(var(--color-primary-light-rgb),0.4)] hover:bg-[rgba(var(--color-primary-rgb),0.08)] hover:text-white"}`}
                    style={navButtonStyle}
                  >
                    {item.label || item.id}
                  </button>
                );
              })}
            </nav>
          )}
        </div>

        <div className="flex items-center" style={headerRightStyle}>
          {primaryAction && (
            <>
              <button
                type="button"
                onClick={() => executeAction(primaryAction)}
                className="hidden md:flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-secondary)] border-2 border-[var(--color-primary-light)] text-white hover:shadow-[0_0_20px_rgba(var(--color-primary-light-rgb),0.45)] transition-all duration-300 text-sm font-bold heading-font"
                style={primaryActionStyle}
              >
                {renderPrimaryActionIcon()}
                <span className="font-bold tracking-wide">{primaryActionLabel}</span>
              </button>
              <button
                type="button"
                onClick={() => executeAction(primaryAction)}
                className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.5)] bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] text-white transition-all hover:shadow-[0_8px_30px_rgba(var(--color-primary-light-rgb),0.4)] md:hidden"
                title={primaryActionLabel}
                style={mobilePrimaryActionStyle}
              >
                {renderPrimaryActionIcon()}
              </button>
            </>
          )}

          {showNotifications && (
            <div className="relative" ref={notificationsRef}>
              <button
                type="button"
                onClick={() => {
                  setIsNotificationsOpen((open) => !open);
                  onNotificationClick?.();
                }}
                className="relative hover:bg-white/10 transition-colors flex items-center justify-center"
                title="Notifications"
                style={utilityButtonStyle}
              >
                {notificationsIconSrc ? (
                  <img src={notificationsIconSrc} alt="" className="w-6 h-6 object-contain" />
                ) : (
                  <DefaultBellIcon className="w-6 h-6 text-[var(--color-primary-light)]" />
                )}
                {notificationCount > 0 && (
                  <div className="absolute top-0 right-0">
                    <div className="w-4 h-4 bg-[var(--color-error)] rounded-full flex items-center justify-center border border-slate-900/60">
                      <span className="text-white text-[10px] font-bold heading-font">{notificationCount > 9 ? "9+" : notificationCount}</span>
                    </div>
                  </div>
                )}
              </button>

              {isNotificationsOpen && (
                <div className="absolute right-0 top-full mt-2 w-64 bg-slate-900/95 border border-[rgba(var(--color-primary-light-rgb),0.4)] backdrop-blur-xl overflow-hidden z-50" style={dropdownPanelStyle}>
                  <div className="p-4 border-b border-[rgba(var(--color-primary-light-rgb),0.2)]">
                    <div className="text-white font-semibold text-sm heading-font uppercase tracking-[0.16em]">Notifications</div>
                    <div className="text-[rgba(var(--color-primary-light-rgb),0.7)] text-xs mt-1 transmission-typing-font">
                      {notificationCount > 0 ? `${notificationCount} unread` : "All clear"}
                    </div>
                  </div>
                  <div className="p-4 text-sm text-[rgba(226,232,240,0.82)] transmission-typing-font">
                    {notificationCount > 0 ? "Unread alerts are available." : notificationsConfig.emptyText || "No notifications"}
                  </div>
                  <div className="mx-3 mb-3 flex flex-col gap-2">
                    {notificationsPath && (
                      <button
                        type="button"
                        onClick={() => {
                          handleNavigationItem({ path: notificationsPath });
                          setIsNotificationsOpen(false);
                        }}
                        className="flex w-full items-center justify-center rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.35)] px-3 py-2 text-xs font-semibold text-[var(--color-primary-light)] transition hover:bg-[rgba(var(--color-primary-rgb),0.12)] heading-font"
                      >
                        View all notifications
                      </button>
                    )}
                    {notificationCount > 0 && (
                      <button
                        type="button"
                        onClick={handleClearAllNotifications}
                        className="flex w-full items-center justify-center rounded-xl border border-[rgba(var(--color-error-rgb,220,38,38),0.35)] px-3 py-2 text-xs font-semibold text-[var(--color-error,#ef4444)] transition hover:bg-[rgba(var(--color-error-rgb,220,38,38),0.12)] heading-font"
                      >
                        Clear all
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {showProfile && (
            <div className="relative" ref={profileRef}>
              <button
                type="button"
                onClick={() => setIsProfileOpen((open) => !open)}
                className="flex items-center hover:bg-white/10 transition-colors"
                title={profileLabel}
                style={profileButtonStyle}
              >
                <div className="relative">
                  <div className="rounded-full overflow-hidden border border-[rgba(var(--color-primary-light-rgb),0.3)]" style={avatarStyle}>
                    {currentUser.userPhoto ? (
                      <img src={currentUser.userPhoto} alt="User" className="w-full h-full object-cover" />
                    ) : profileIconSrc ? (
                      <img src={profileIconSrc} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center text-xs font-bold text-[var(--color-primary-light)] bg-white/5">
                        {(profileLabel?.[0] || "?").toUpperCase()}
                      </span>
                    )}
                  </div>
                  <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-[var(--color-success)] rounded-full border border-slate-900"></div>
                </div>
                <div className="hidden lg:block text-left">
                  <div className="text-[var(--color-primary-light)] text-xs font-medium truncate max-w-[10rem] heading-font uppercase tracking-[0.16em]">{profileLabel}</div>
                </div>
                <ChevronIcon open={isProfileOpen} />
              </button>

              {isProfileOpen && (
                <div className="absolute right-0 top-full mt-2 w-64 bg-slate-900/95 border border-[rgba(var(--color-primary-light-rgb),0.4)] backdrop-blur-xl overflow-hidden z-50" style={dropdownPanelStyle}>
                  <div className="p-4 border-b border-[rgba(var(--color-primary-light-rgb),0.2)]">
                    <div className="flex items-center space-x-3">
                      <div className="relative">
                        <div className="rounded-full overflow-hidden border-2 border-[rgba(var(--color-primary-light-rgb),0.4)]" style={avatarLargeStyle}>
                          {currentUser.userPhoto ? (
                            <img src={currentUser.userPhoto} alt="User" className="w-full h-full object-cover" />
                          ) : profileIconSrc ? (
                            <img src={profileIconSrc} alt="" className="w-full h-full object-cover" />
                          ) : (
                            <span className="flex h-full w-full items-center justify-center text-lg font-bold text-[var(--color-primary-light)] bg-white/5">
                              {(profileLabel?.[0] || "?").toUpperCase()}
                            </span>
                          )}
                        </div>
                        <div className="absolute -bottom-1 -right-1 w-4 h-4 bg-[var(--color-success)] rounded-full border-2 border-slate-900"></div>
                      </div>
                      <div className="min-w-0">
                        <div className="text-white font-semibold truncate heading-font uppercase tracking-[0.12em]">{profileLabel}</div>
                        {profileSubLabel && <div className="text-[rgba(var(--color-primary-light-rgb),0.7)] text-xs truncate transmission-typing-font">{profileSubLabel}</div>}
                      </div>
                    </div>
                  </div>

                  <div className="p-2">
                    {profileMenu.length === 0 && (
                      <div className="px-3 py-2.5 text-sm text-[rgba(226,232,240,0.72)] transmission-typing-font">
                        No profile actions available.
                      </div>
                    )}

                    {profileMenu.map((item, index) => {
                      if (item.type === "divider") {
                        return <div key={item.id || `divider-${index}`} className="border-t border-[rgba(var(--color-primary-light-rgb),0.2)] my-2" />;
                      }

                      const isDanger = item.variant === "danger" || item.action === "signout" || item.id === "signout";
                      return (
                        <button
                          key={item.id || `${item.label}-${index}`}
                          type="button"
                          onClick={() => executeAction(item)}
                          className={`w-full px-3 py-2.5 text-left rounded-xl transition-colors flex items-center gap-3 ${isDanger
                            ? "text-[var(--color-error)] hover:bg-[rgba(var(--color-error-rgb),0.1)]"
                            : "text-white hover:bg-[rgba(var(--color-primary-light-rgb),0.1)]"}`}
                        >
                          <ActionIcon icon={item.icon} className="h-4 w-4" />
                          <span className="text-sm heading-font uppercase tracking-[0.12em]">{item.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default Header;
