import React from 'react';
import {
  RiAddCircleLine,
  RiAppsFill,
  RiBarChart2Line,
  RiCustomerServiceFill,
  RiDashboardFill,
  RiFileList3Fill,
  RiHistoryFill,
  RiHome4Line,
  RiMoneyDollarCircleFill,
  RiNotification3Line,
  RiPlugLine,
  RiPulseLine,
  RiServerFill,
  RiSettings3Fill,
  RiStore2Line,
  RiUser3Fill,
} from 'react-icons/ri';

// The shell already uses these icon hints for workspace and admin navigation.
const ICONS = {
  alerts: RiNotification3Line,
  analytics: RiBarChart2Line,
  apps: RiAppsFill,
  workspace: RiAppsFill,
  home: RiHome4Line,
  create: RiAddCircleLine,
  'create-app': RiAddCircleLine,
  notifications: RiNotification3Line,
  profile: RiUser3Fill,
  account: RiUser3Fill,
  billing: RiMoneyDollarCircleFill,
  chart: RiFileList3Fill,
  dashboard: RiDashboardFill,
  history: RiHistoryFill,
  operations: RiServerFill,
  plug: RiPlugLine,
  pulse: RiPulseLine,
  server: RiServerFill,
  settings: RiSettings3Fill,
  store: RiStore2Line,
  support: RiCustomerServiceFill,
  users: RiUser3Fill,
};

const ICON_FILE_RE = /\.(svg|png|jpe?g|gif|webp|ico)$/i;

// A navigation item id may stand in for a missing icon hint only when it names
// one of the icons above. Ids derived from a path or href must never reach the
// asset branch below, where they would become a mask URL that blanks the glyph.
export const hasNamedIcon = (id) => (
  typeof id === 'string' && Object.prototype.hasOwnProperty.call(ICONS, id)
);

export default function ShellNavigationIcon({ icon, fallback }) {
  const NamedIcon = hasNamedIcon(icon) ? ICONS[icon] : null;
  if (NamedIcon) return <NamedIcon aria-hidden="true" className="shell-mobile-bottom-icon" />;

  const source = typeof icon === 'string' && icon
    ? icon.startsWith('/') || icon.startsWith('http')
      ? icon
      : ICON_FILE_RE.test(icon) ? `/assets/${icon}` : null
    : null;
  if (source) {
    return (
      <span
        aria-hidden="true"
        className="shell-mobile-bottom-icon bg-current"
        style={{
          mask: `url(${source}) center / contain no-repeat`,
          WebkitMask: `url(${source}) center / contain no-repeat`,
        }}
      />
    );
  }

  return <>{fallback}</>;
}
