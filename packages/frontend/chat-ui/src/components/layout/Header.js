import React, { useState, useRef, useEffect } from "react";
import { DEFAULT_HEADER_CONFIG } from "../../styles/themeProvider";
import { useNavigation } from "../../providers/NavigationProvider";
import { useNavigationActions } from "../../navigation/useNavigationActions";
import "./header-styles.css";

const ICON_FILE_RE = /\.(svg|png|jpe?g|gif|webp|ico)$/i;

const resolveIconSource = (iconValue) => {
  if (!iconValue || typeof iconValue !== "string") return null;
  if (iconValue.startsWith("/") || iconValue.startsWith("http")) return iconValue;
  if (ICON_FILE_RE.test(iconValue)) return `/assets/${iconValue}`;
  return null;
};

/**
 * ActionIcon — renders a brand-owned asset file as a color-inheriting icon.
 * The icon value MUST be a file path (absolute, http, or a filename resolved
 * from /assets/). Named string shortcuts ("bell", "sparkle", etc.) are NOT
 * supported — set the actual filename in brand.json.
 */
const ActionIcon = ({ icon, className = "w-5 h-5" }) => {
  const src = resolveIconSource(icon);
  if (!src) {
    console.warn(`⚠️ [HEADER] ActionIcon received icon="${icon}" which is not a resolvable asset path. Set a valid filename (e.g. "sparkle.svg") in brand.json.`);
    return null;
  }
  // mask-image renders the SVG as a shape filled with currentColor,
  // so the icon inherits theme color from its parent element.
  return (
    <span
      aria-hidden="true"
      className={`inline-block ${className}`}
      style={{
        backgroundColor: 'currentColor',
        maskImage: `url(${src})`,
        WebkitMaskImage: `url(${src})`,
        maskSize: 'contain',
        WebkitMaskSize: 'contain',
        maskRepeat: 'no-repeat',
        WebkitMaskRepeat: 'no-repeat',
        maskPosition: 'center',
        WebkitMaskPosition: 'center',
      }}
    />
  );
};

const Header = ({
  user = null,
  chatTheme = null,
  themeLoading = false,
  onNotificationClick = () => {},
  onAction = () => {},
}) => {
  const { topNav } = useNavigation();
  const handleNavigationItem = useNavigationActions();
  const topNavItems = Array.isArray(topNav?.items)
    ? [...topNav.items].sort((a, b) => (a?.order ?? 0) - (b?.order ?? 0))
    : [];
  // Resolve header config from theme with defaults
  const headerConfig = {
    ...DEFAULT_HEADER_CONFIG,
    ...chatTheme?.header,
  };
  const logoConfig   = { ...DEFAULT_HEADER_CONFIG.logo, ...headerConfig?.logo };
  const brandConfig  = { ...{ name: 'MozaiksAI' }, ...chatTheme?.branding };

  // Profile and notifications come from their own top-level ui.json sections.
  // Icons MUST be set as asset filenames in ui.json — no built-in fallbacks.
  const profileIcon         = chatTheme?.profile?.icon  ? resolveIconSource(chatTheme.profile.icon)  : null;
  const showProfile         = chatTheme?.profile?.show  !== false;
  const profileDefaultLabel = chatTheme?.profile?.defaultLabel || 'User';
  const profileSublabel     = chatTheme?.profile?.sublabel     || null;
  const profileMenu         = chatTheme?.profile?.menu         || [];
  if (!themeLoading && showProfile && !profileIcon) {
    console.warn('⚠️ [HEADER] profile.show is true but profile.icon is not set in ui.json — profile button hidden');
  }

  const notificationIcon  = chatTheme?.notifications?.icon ? resolveIconSource(chatTheme.notifications.icon) : null;
  const showNotifications = chatTheme?.notifications?.show !== false;
  if (!themeLoading && showNotifications && !notificationIcon) {
    console.warn('⚠️ [HEADER] notifications.show is true but notifications.icon is not set in ui.json — notification button hidden');
  }

  // Default user if none provided (for standalone mode)
  const defaultUser = {
    id: "56132",
    firstName: "John Doe",
    userPhoto: null
  };

  const currentUser = user || defaultUser;
  const [isProfileDropdownOpen, setIsProfileDropdownOpen] = useState(false);
  const [isNotificationDropdownOpen, setIsNotificationDropdownOpen] = useState(false);
  const [notificationCount, setNotificationCount] = useState(3);
  const [isScrolled, setIsScrolled] = useState(false);
  const [openActionMenuId, setOpenActionMenuId] = useState(null);
  const dropdownRef = useRef(null);
  const headerRef = useRef(null);

  // Handle scroll effect for dynamic blur
  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 10);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Notification count updates
  useEffect(() => {
    const interval = setInterval(() => {
      setNotificationCount(prev => Math.max(0, prev + Math.floor(Math.random() * 3) - 1));
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  // Close dropdowns when clicking outside or pressing Escape
  useEffect(() => {
    const handleGlobalPointer = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        if (isProfileDropdownOpen) setIsProfileDropdownOpen(false);
      }
      if (openActionMenuId && headerRef.current && !headerRef.current.contains(e.target)) {
        setOpenActionMenuId(null);
      }
    };
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (isProfileDropdownOpen) setIsProfileDropdownOpen(false);
        if (isNotificationDropdownOpen) setIsNotificationDropdownOpen(false);
        if (openActionMenuId) setOpenActionMenuId(null);
      }
    };
    document.addEventListener('mousedown', handleGlobalPointer);
    document.addEventListener('touchstart', handleGlobalPointer, { passive: true });
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleGlobalPointer);
      document.removeEventListener('touchstart', handleGlobalPointer);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isProfileDropdownOpen, isNotificationDropdownOpen, openActionMenuId]);

  const toggleProfileDropdown = () => {
    setIsProfileDropdownOpen(!isProfileDropdownOpen);
    if (isNotificationDropdownOpen) {
      setIsNotificationDropdownOpen(false);
    }
  };

  const toggleNotificationDropdown = () => {
    setIsNotificationDropdownOpen(!isNotificationDropdownOpen);
    if (isProfileDropdownOpen) {
      setIsProfileDropdownOpen(false);
    }
    onNotificationClick();
  };

  // Render a single profile menu item from ui.json profile.menu declaration
  const renderProfileMenuItem = (item) => {
    if (!item) return null;
    if (item.type === 'divider') {
      return <div key={item.id} className="border-t border-[rgba(var(--color-primary-light-rgb),0.2)] my-2" />;
    }
    const isDanger = item.variant === 'danger';
    return (
      <button
        key={item.id}
        onClick={() => onAction(item.action || item.id, item)}
        className={`w-full px-3 py-2.5 text-left rounded-xl transition-colors flex items-center gap-3 ${
          isDanger
            ? 'text-[var(--color-error)] hover:bg-[rgba(var(--color-error-rgb),0.1)]'
            : 'text-white hover:bg-[rgba(var(--color-primary-light-rgb),0.1)]'
        }`}
      >
        {item.icon && <ActionIcon icon={item.icon} className="w-4 h-4" />}
        <span className="oxanium text-sm">{item.label}</span>
      </button>
    );
  };

  // --- Config-driven Logo ---
  const LogoSection = () => {
    const Wrapper = logoConfig.href ? 'a' : 'div';
    const wrapperProps = logoConfig.href
      ? { href: logoConfig.href, target: '_blank', rel: 'noopener noreferrer' }
      : {};
    return (
      <Wrapper {...wrapperProps} className="flex items-center gap-2">
        {logoConfig.src && <img src={logoConfig.src} className="h-7 w-7" alt={logoConfig.alt || brandConfig.name || 'Logo'} />}
        {logoConfig.wordmark && <img src={logoConfig.wordmark} className="h-7 opacity-90" alt={brandConfig.name || 'Brand'} />}
      </Wrapper>
    );
  };

  // --- Config-driven Action Buttons ---
  const handleActionClick = (action) => {
    if (action?.items && Array.isArray(action.items) && action.items.length > 0) {
      setOpenActionMenuId((prev) => (prev === action.id ? null : action.id));
      return;
    }
    onAction(action.id, action);
  };

  const handleActionItemClick = (action, item) => {
    setOpenActionMenuId(null);
    onAction(item?.id || action.id, item || action);
  };

  const ActionButtons = () => {
    const actions = headerConfig.actions || DEFAULT_HEADER_CONFIG.actions || [];
    return actions.filter(a => a.visible !== false).map(action => (
      <React.Fragment key={action.id}>
        {/* Desktop action button */}
        <button
          onClick={() => handleActionClick(action)}
          className="hidden md:flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-secondary)] border-2 border-[var(--color-primary-light)] text-white oxanium hover:shadow-[0_0_20px_rgba(51,240,250,0.5)] transition-all duration-300 text-sm font-bold"
          title={action.label || action.id}
          style={{ boxShadow: '0 0 10px rgba(51,240,250,0.3)' }}
        >
          <ActionIcon icon={action.icon} className="w-5 h-5" />
          <span className="font-bold tracking-wide">{action.label || action.id}</span>
        </button>
        {openActionMenuId === action.id && Array.isArray(action.items) && action.items.length > 0 && (
          <div className="hidden md:block absolute right-16 top-full mt-2 w-56 rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.35)] bg-[rgba(5,10,24,0.96)] backdrop-blur-xl shadow-[0_20px_60px_rgba(2,6,23,0.6)] overflow-hidden z-50">
            <div className="flex flex-col py-2">
              {action.items.map((item) => (
                <button
                  key={item.id || item.label}
                  type="button"
                  onClick={() => handleActionItemClick(action, item)}
                  className="w-full text-left px-4 py-2.5 text-sm text-[rgba(226,232,240,0.9)] hover:bg-white/10 transition"
                >
                  {item.label || item.id}
                </button>
              ))}
            </div>
          </div>
        )}
        {/* Mobile action button */}
        <button
          onClick={() => handleActionClick(action)}
          className="md:hidden inline-flex items-center justify-center w-11 h-11 rounded-2xl bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] border border-[rgba(var(--color-primary-light-rgb),0.5)] text-white hover:shadow-[0_8px_30px_rgba(var(--color-primary-light-rgb),0.4)] transition-all"
          title={action.label || action.id}
        >
          <ActionIcon icon={action.icon} className="w-5 h-5" />
        </button>
      </React.Fragment>
    ));
  };

  return (
    <header ref={headerRef} className={`
      fixed top-0 left-0 right-0 z-50 transition-all duration-300
      ${isScrolled ? 'backdrop-blur-md bg-black/25' : 'backdrop-blur-md bg-black/15'}
      border-b border-[rgba(var(--color-primary-rgb),0.1)]
    `}>
      {/* Main header content - single compact row */}
      <div className="relative h-14 md:h-16 flex items-center justify-between px-4 md:px-6 lg:px-8">
        {/* LEFT: Brand (config-driven) */}
        <div className="flex items-center gap-3 md:gap-4">
          <LogoSection />
          {topNavItems.length > 0 && (
            <nav className="hidden md:flex items-center gap-2">
              {topNavItems.map((item) => (
                <button
                  key={item.id || item.label}
                  type="button"
                  onClick={() => handleNavigationItem(item)}
                  className="px-3 py-1.5 rounded-lg text-xs font-semibold tracking-wide text-[rgba(226,232,240,0.9)] border border-transparent hover:border-[rgba(var(--color-primary-light-rgb),0.4)] hover:bg-white/10 transition"
                >
                  {item.label || item.id}
                </button>
              ))}
            </nav>
          )}
        </div>

        {/* RIGHT: Commander, notifications, actions */}
        <div className="flex items-center gap-2 md:gap-3">
          {/* Commander (conditionally rendered) */}
          {showProfile && (
            <div className="relative" ref={dropdownRef}>
              <button onClick={toggleProfileDropdown} className="flex items-center gap-2 p-1.5 rounded-lg hover:bg-white/10 transition-colors" title="Command Profile">
                <div className="relative">
                  <div className="w-8 h-8 rounded-full overflow-hidden border border-[rgba(var(--color-primary-light-rgb),0.3)]">
                    {currentUser.userPhoto ? (
                      <img src={currentUser.userPhoto} alt="User" className="w-full h-full object-cover" />
                    ) : profileIcon ? (
                      <img src={profileIcon} alt="profile" className="w-full h-full object-cover" />
                    ) : (
                      <span className="w-full h-full flex items-center justify-center text-[var(--color-primary-light)] text-xs font-bold">
                        {(currentUser.firstName?.[0] || currentUser.username?.[0] || '?').toUpperCase()}
                      </span>
                    )}
                  </div>
                  <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-[var(--color-success)] rounded-full border border-slate-900"></div>
                </div>
                <div className="hidden lg:block text-left">
                  <div className="text-[var(--color-primary-light)] text-slate-200 text-xs font-medium oxanium">{currentUser.firstName || profileDefaultLabel}</div>
                </div>
                <svg className="w-3 h-3 text-[rgba(var(--color-primary-light-rgb),0.6)]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {isProfileDropdownOpen && (
                <div className="absolute right-0 top-full mt-2 w-64 bg-slate-900/95 border border-[rgba(var(--color-primary-light-rgb),0.4)] rounded-2xl backdrop-blur-xl overflow-hidden z-50">
                  <div className="relative p-4 border-b border-[rgba(var(--color-primary-light-rgb),0.2)]">
                    <div className="flex items-center space-x-3">
                      <div className="relative">
                        <div className="w-12 h-12 rounded-full overflow-hidden border-2 border-[rgba(var(--color-primary-light-rgb),0.4)]">
                          {currentUser.userPhoto ? (
                            <img src={currentUser.userPhoto} alt="User" className="w-full h-full object-cover" />
                          ) : profileIcon ? (
                            <img src={profileIcon} alt="profile" className="w-full h-full object-cover" />
                          ) : (
                            <span className="w-full h-full flex items-center justify-center text-[var(--color-primary-light)] text-sm font-bold">
                              {(currentUser.firstName?.[0] || currentUser.username?.[0] || '?').toUpperCase()}
                            </span>
                          )}
                        </div>
                        <div className="absolute -bottom-1 -right-1 w-4 h-4 bg-[var(--color-success)] rounded-full border-2 border-slate-900"></div>
                      </div>
                      <div>
                        <div className="text-[var(--color-primary-light)] text-white font-semibold oxanium">{currentUser.firstName || profileDefaultLabel}</div>
                        {profileSublabel && <div className="text-[rgba(var(--color-primary-light-rgb),0.7)] text-xs oxanium">{profileSublabel}</div>}
                      </div>
                    </div>
                  </div>
                  <div className="relative p-2">
                    {profileMenu.map(renderProfileMenuItem)}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Notifications (conditionally rendered — requires notifications.icon in brand.json) */}
          {showNotifications && notificationIcon && (
            <button onClick={toggleNotificationDropdown} className="relative p-1.5 rounded-lg hover:bg-white/10 transition-colors flex items-center justify-center" title="Mission Alerts">
              <ActionIcon icon={notificationIcon} className="w-6 h-6 text-[var(--color-primary-light)]" />
              {notificationCount > 0 && (
                <div className="absolute top-0 right-0">
                  <div className="w-4 h-4 bg-[var(--color-error)] rounded-full flex items-center justify-center border border-slate-900/60">
                    <span className="text-white text-[10px] font-bold oxanium">{notificationCount}</span>
                  </div>
                </div>
              )}
            </button>
          )}

          {/* Config-driven action buttons */}
          <ActionButtons />
        </div>
      </div>

      {/* Mobile spacing placeholder */}
      <div className="md:hidden px-4 pb-3" />
    </header>
  );
};

export default Header;
