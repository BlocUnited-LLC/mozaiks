import React, { useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { useNavigation } from "../../providers/NavigationProvider";
import { useNavigationActions } from "../../navigation/useNavigationActions";
import { deriveShellActionContext, isShellItemVisible, resolveShellActions } from "../../navigation/shellActions";
import { useChatUI } from "../../context/ChatUIContext";
import { useAppEventBus } from "../../ui/hooks/useAppEventBus.js";
import { fetchNotificationCount } from "./notificationApi.js";
import "./header-styles.css";

const buildAutoItems = ({ headerPages, header, notifications, profile, actionContext }) => {
  const items = [];
  const roles = actionContext?.roles || [];

  if (Array.isArray(headerPages)) {
    for (const item of headerPages) {
      if (items.length >= 3) break;
      if (isShellItemVisible(item, roles) && item.path) {
        items.push({
          id: item.id || item.path,
          label: item.label || item.id || "Page",
          action: "navigate",
          path: item.path,
        });
      }
    }
  }

  const resolvedActions = Array.isArray(header?.actions)
    ? resolveShellActions(header.actions, actionContext)
    : [];
  const primaryAction = resolvedActions.find((item) => item?.path || item?.href || item?.trigger);
  if (primaryAction) {
    items.push({
      id: primaryAction.id || primaryAction.path || primaryAction.href,
      label: primaryAction.label || "Action",
      action: "navigate",
      path: primaryAction.path,
      href: primaryAction.href,
      trigger: primaryAction.trigger,
    });
  }

  if (notifications?.show !== false && notifications?.path) {
    items.push({
      id: "notifications",
      label: "Alerts",
      action: "navigate",
      path: notifications.path,
    });
  }

  const profileItem = Array.isArray(profile?.menu)
    ? profile.menu.find((item) => (
      isShellItemVisible(item, roles) &&
      item.action !== "signout" &&
      item.action !== "signin" &&
      item.path
    ))
    : null;
  if (profile?.show !== false && profileItem) {
    items.push({
      id: profileItem.id || "profile",
      label: profileItem.label || "Account",
      action: "navigate",
      path: profileItem.path,
    });
  }

  const seen = new Set();
  return items.filter((item) => {
    const key = item.path || item.href || item.id;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 5);
};

const MobileBottomBar = ({ route = null, shellMode = null }) => {
  const location = useLocation();
  const handleNavigationItem = useNavigationActions();
  const { login, logout, user } = useChatUI();
  const { mobile, headerPages, header, notifications, profile } = useNavigation();
  const [notificationCount, setNotificationCount] = useState(0);
  const actionContext = useMemo(
    () => deriveShellActionContext({ location, route, shellMode, user }),
    [location.pathname, location.search, route, shellMode, user]
  );

  React.useEffect(() => {
    if (notifications?.show === false) return undefined;
    const controller = new AbortController();
    let mounted = true;

    const loadNotificationCount = () => {
      fetchNotificationCount({ signal: controller.signal })
        .then((payload) => {
          if (!mounted || !payload) return;
          const count = Number(payload.unread_count ?? payload.count ?? 0);
          if (Number.isFinite(count)) setNotificationCount(Math.max(0, count));
        })
        .catch(() => {
          if (mounted) setNotificationCount(0);
        });
    };

    loadNotificationCount();
    const intervalId = window.setInterval(loadNotificationCount, 15000);
    return () => {
      mounted = false;
      window.clearInterval(intervalId);
      controller.abort();
    };
  }, [notifications?.show]);

  useAppEventBus('notification.count_changed', () => {
    if (notifications?.show === false) return;
    fetchNotificationCount()
      .then((payload) => {
        if (!payload) return;
        const count = Number(payload?.unread_count ?? payload?.count ?? 0);
        if (Number.isFinite(count)) setNotificationCount(Math.max(0, count));
      })
      .catch(() => {});
  });

  const bottomBar = mobile?.bottomBar || {};
  const configuredItems = Array.isArray(bottomBar.items)
    ? bottomBar.items.filter((item) => isShellItemVisible(item, actionContext.roles || []))
    : [];
  const items = useMemo(
    () => configuredItems.length > 0
      ? configuredItems.slice(0, 5)
      : buildAutoItems({ headerPages, header, notifications, profile, actionContext }),
    [actionContext, configuredItems, header, headerPages, notifications, profile]
  );

  if (bottomBar.visible === false || items.length === 0) return null;

  const execute = async (item) => {
    if (item.action === "signout" || item.id === "signout") {
      await logout?.();
      return;
    }
    if (item.action === "signin" || item.id === "signin") {
      await login?.();
      return;
    }
    handleNavigationItem(item);
  };

  return (
    <nav className="shell-mobile-bottom-bar" aria-label="Mobile app navigation">
      {items.map((item) => {
        const active = item.path && location.pathname === item.path;
        const showBadge = item.id === "notifications" && notificationCount > 0;
        return (
          <button
            key={item.id || item.path || item.href || item.label}
            type="button"
            onClick={() => execute(item)}
            className={`shell-mobile-bottom-item${active ? " is-active" : ""}`}
          >
            <span className="shell-mobile-bottom-glyph" aria-hidden="true">
              {(item.iconLabel || item.label || item.id || "?").slice(0, 1).toUpperCase()}
              {showBadge && <span className="shell-mobile-bottom-badge">{notificationCount > 9 ? "9+" : notificationCount}</span>}
            </span>
            <span className="shell-mobile-bottom-label">{item.label || item.id}</span>
          </button>
        );
      })}
    </nav>
  );
};

export default MobileBottomBar;
