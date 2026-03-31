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
 */
const ActionIcon = ({ icon, className = "w-5 h-5" }) => {
  const src = resolveIconSource(icon);
  if (!src) return null;
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
  onAction = () => {},
}) => {
  const { pages, headerPages, header: navHeader, profile: navProfile } = useNavigation();
  const handleNavigationItem = useNavigationActions();

  const headerConfig = {
    ...DEFAULT_HEADER_CONFIG,
    ...navHeader,
  };
  const logoConfig = { ...DEFAULT_HEADER_CONFIG.logo, ...headerConfig?.logo };
  const brandConfig = { ...{ name: 'mozaiksai' }, ...chatTheme?.branding };

  const profileIcon = navProfile?.icon ? resolveIconSource(navProfile.icon) : null;
  const showProfile = navProfile?.show !== false;
  const profileDefaultLabel = navProfile?.defaultLabel || 'User';

  const currentUser = user || { id: 'anonymous', firstName: 'Guest', userPhoto: null };
  const [isProfileDropdownOpen, setIsProfileDropdownOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  const dropdownRef = useRef(null);
  const headerRef = useRef(null);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 10);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    const handleGlobalPointer = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        if (isProfileDropdownOpen) setIsProfileDropdownOpen(false);
      }
    };
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isProfileDropdownOpen) {
        setIsProfileDropdownOpen(false);
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
  }, [isProfileDropdownOpen]);

  const toggleProfileDropdown = () => {
    setIsProfileDropdownOpen(!isProfileDropdownOpen);
  };

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

  return (
    <header ref={headerRef} className={`
      fixed top-0 left-0 right-0 z-50 pt-[env(safe-area-inset-top)] transition-all duration-300
      ${isScrolled ? 'backdrop-blur-md bg-black/25' : 'backdrop-blur-md bg-black/15'}
      border-b border-[rgba(var(--color-primary-rgb),0.1)]
    `}>
      <div className="relative h-14 md:h-16 flex items-center justify-between px-4 md:px-6 lg:px-8">
        {/* LEFT: Brand */}
        <div className="flex items-center gap-3 md:gap-4">
          <LogoSection />
          {headerPages.length > 0 && (
            <nav className="hidden md:flex items-center gap-2">
              {headerPages.map((item) => (
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

        {/* RIGHT: Profile */}
        <div className="flex items-center gap-2 md:gap-3">
          {showProfile && profileIcon && (
            <div className="relative" ref={dropdownRef}>
              <button onClick={toggleProfileDropdown} className="flex items-center gap-2 p-1.5 rounded-lg hover:bg-white/10 transition-colors" title="Profile">
                <div className="relative">
                  <div className="w-8 h-8 rounded-full overflow-hidden border border-[rgba(var(--color-primary-light-rgb),0.3)]">
                    {currentUser.userPhoto ? (
                      <img src={currentUser.userPhoto} alt="User" className="w-full h-full object-cover" />
                    ) : profileIcon ? (
                      <img src={profileIcon} alt="profile" className="w-full h-full object-cover" />
                    ) : (
                      <span className="w-full h-full flex items-center justify-center text-[var(--color-primary-light)] text-xs font-bold">
                        {(currentUser.firstName?.[0] || '?').toUpperCase()}
                      </span>
                    )}
                  </div>
                </div>
                <div className="hidden lg:block text-left">
                  <div className="text-slate-200 text-xs font-medium oxanium">{currentUser.firstName || profileDefaultLabel}</div>
                </div>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default Header;
