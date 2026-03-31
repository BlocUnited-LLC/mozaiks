/**
 * Config Validator
 *
 * Validates the declarative config file (theme_config.json)
 * fetched from the backend API at startup and surfaces human-readable errors.
 * Designed for non-technical founders — messages explain WHAT is wrong and HOW to fix it.
 *
 * Runs once during app init. Returns an array of { level, file, message }
 * objects. Levels: 'error' (will break), 'warn' (degraded), 'info'.
 *
 * @module @mozaiks/chat-ui/config/validateConfig
 */

const ICON_FILE_RE = /\.(svg|png|jpe?g|gif|webp|ico)$/i;
const HEX_COLOR_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const URL_RE = /^(https?:\/\/|\/)/;

// ---------------------------------------------------------------------------
// Individual section validators
// ---------------------------------------------------------------------------

function validateThemeConfig(config) {
  const issues = [];
  const file = 'theme_config.json';

  if (!config || typeof config !== 'object') {
    issues.push({ level: 'error', file, message: 'Config is empty or not valid JSON.' });
    return issues;
  }

  // Identity
  const identity = config.identity;
  if (!identity || !identity.name) {
    issues.push({ level: 'warn', file, message: 'Missing "identity.name". Your app will show as "App" in the browser tab.' });
  }

  // Assets
  if (!config.assets) {
    issues.push({ level: 'error', file, message: 'Missing "assets" block. Add at least: { "assets": { "logo": "your-logo.svg" } }' });
  } else {
    if (!config.assets.logo) {
      issues.push({ level: 'warn', file, message: 'Missing "assets.logo". The header will have no logo image.' });
    } else if (!ICON_FILE_RE.test(config.assets.logo) && !URL_RE.test(config.assets.logo)) {
      issues.push({ level: 'error', file, message: `assets.logo="${config.assets.logo}" doesn't look like a file. Use a filename like "logo.svg" (placed in platform/brand/assets/).` });
    }
    if (!config.assets.chatbackgroundImage) {
      issues.push({ level: 'info', file, message: 'No "assets.chatbackgroundImage" set. The chat background will be a solid color.' });
    }
  }

  // Colors
  if (!config.colors) {
    issues.push({ level: 'warn', file, message: 'Missing "colors" block. The default blue/indigo palette will be used.' });
  } else {
    const requiredColors = ['primary', 'secondary'];
    for (const name of requiredColors) {
      const c = config.colors[name];
      if (!c) {
        issues.push({ level: 'warn', file, message: `Missing "colors.${name}". A fallback color will be used.` });
      } else if (typeof c === 'object') {
        if (!c.main) {
          issues.push({ level: 'warn', file, message: `"colors.${name}.main" is missing. Each color needs at least a "main" hex value like "#06b6d4".` });
        } else if (!HEX_COLOR_RE.test(c.main)) {
          issues.push({ level: 'error', file, message: `"colors.${name}.main" = "${c.main}" is not a valid hex color. Use format: "#06b6d4".` });
        }
      }
    }
  }

  // Fonts
  if (!config.fonts) {
    issues.push({ level: 'info', file, message: 'No "fonts" block. System fonts will be used.' });
  }

  // UI chrome
  const ui = config.ui;
  if (ui) {
    // Header
    if (ui.header) {
      const logo = ui.header.logo;
      if (logo) {
        if (logo.src && !ICON_FILE_RE.test(logo.src) && !URL_RE.test(logo.src)) {
          issues.push({ level: 'error', file, message: `ui.header.logo.src="${logo.src}" is not a valid asset filename. Use a filename like "logo.svg" or a full URL.` });
        }
      }

      if (Array.isArray(ui.header.actions)) {
        ui.header.actions.forEach((action, i) => {
          if (action.icon && !ICON_FILE_RE.test(action.icon) && !URL_RE.test(action.icon)) {
            issues.push({ level: 'error', file, message: `ui.header.actions[${i}].icon="${action.icon}" is not a valid asset filename. Use "sparkle.svg" not "sparkle".` });
          }
        });
      }
    }

    // Profile
    if (ui.profile) {
      if (ui.profile.show !== false && ui.profile.icon) {
        if (!ICON_FILE_RE.test(ui.profile.icon) && !URL_RE.test(ui.profile.icon)) {
          issues.push({ level: 'error', file, message: `ui.profile.icon="${ui.profile.icon}" is not a valid asset filename. Use "profile.svg" (placed in platform/brand/assets/).` });
        }
      }
      if (Array.isArray(ui.profile.menu)) {
        ui.profile.menu.forEach((item, i) => {
          if (item.type === 'divider') return;
          if (!item.id) {
            issues.push({ level: 'warn', file, message: `ui.profile.menu[${i}] is missing an "id" field. Each menu item needs a unique id.` });
          }
          if (!item.label) {
            issues.push({ level: 'warn', file, message: `ui.profile.menu[${i}] is missing a "label". The menu item won't have visible text.` });
          }
          if (item.icon && !ICON_FILE_RE.test(item.icon) && !URL_RE.test(item.icon)) {
            issues.push({ level: 'error', file, message: `ui.profile.menu[${i}].icon="${item.icon}" is not a valid filename. Use "settings.svg" not "settings".` });
          }
          if (item.action === 'navigate' && !item.href && !item.path) {
            issues.push({ level: 'error', file, message: `ui.profile.menu[${i}] has action="navigate" but no "href". Where should it navigate to?` });
          }
        });
      }
    }

    // Notifications
    if (ui.notifications?.show !== false && ui.notifications?.icon) {
      if (!ICON_FILE_RE.test(ui.notifications.icon) && !URL_RE.test(ui.notifications.icon)) {
        issues.push({ level: 'error', file, message: `ui.notifications.icon="${ui.notifications.icon}" is not a valid asset filename. Use "notifications.svg".` });
      }
    }
  }

  return issues;
}

function validateAuth(auth, app = {}) {
  const issues = [];
  const file = 'app.json → auth';
  const topLevelAdmins = Array.isArray(app.admins) ? app.admins : [];

  if (!auth || typeof auth !== 'object') {
    issues.push({ level: 'info', file, message: 'No auth config. Auth is handled by the host app or authAdapter prop.' });
    return issues;
  }

  if (!auth.provider) {
    issues.push({ level: 'info', file, message: 'No "provider" set. Auth adapter must be supplied by the host app.' });
  }

  // Roles
  if (auth.roles) {
    if (!auth.roles.admin) {
      issues.push({ level: 'info', file, message: 'roles.admin not set. The admin role name defaults to "admin".' });
    }
    if ((!Array.isArray(auth.roles.adminEmails) || auth.roles.adminEmails.length === 0) && topLevelAdmins.length === 0) {
      issues.push({ level: 'info', file, message: 'No admin users declared. Add app.json → admins to make the Admin Portal easier to bootstrap.' });
    }
  }

  return issues;
}

function validateAppConfig(app) {
  const issues = [];
  const file = 'app.json';

  if (!app || typeof app !== 'object') {
    issues.push({ level: 'error', file, message: 'File is empty or not valid JSON. This file is required.' });
    return issues;
  }

  if (!app.appName) {
    issues.push({ level: 'warn', file, message: 'Missing "appName". Your app will be titled "My App".' });
  }
  if (!app.targets || typeof app.targets !== 'object') {
    issues.push({ level: 'warn', file, message: 'Missing "targets". The app will default to web enabled and mobile disabled.' });
  }
  if (app.authRequired !== undefined && typeof app.authRequired !== 'boolean') {
    issues.push({ level: 'error', file, message: '"authRequired" must be true or false.' });
  }
  if (app.admins && (!Array.isArray(app.admins) || app.admins.some((value) => typeof value !== 'string'))) {
    issues.push({ level: 'error', file, message: '"admins" must be an array of email strings.' });
  }

  // Advanced auth overrides remain supported, but are no longer required.
  if (app.auth) {
    issues.push(...validateAuth(app.auth, app));
  }

  return issues;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Fetch and validate all config files. Returns array of issues.
 * Safe to call at any point — catches all fetch/parse errors gracefully.
 */
export async function validateAllConfigs() {
  const results = [];

  const safeLoad = async (url, name) => {
    try {
      const res = await fetch(url);
      if (!res.ok) {
        if (res.status === 404) {
          return { data: null, missing: true };
        }
        throw new Error(`HTTP ${res.status}`);
      }
      return { data: await res.json(), missing: false };
    } catch (err) {
      results.push({
        level: 'error',
        file: name,
        message: `Could not load or parse ${name}: ${err.message}. Check that the file exists and contains valid JSON.`,
      });
      return { data: null, missing: false };
    }
  };

  // Resolve backend base URL
  const coreUrl = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL) || '';
  const baseUrl = coreUrl.replace(/\/+$/, '');

  // Load theme config from backend API
  const themeResult = await safeLoad(
    baseUrl ? `${baseUrl}/api/theme-config` : '/api/theme-config',
    'theme_config.json',
  );

  if (themeResult.data) results.push(...validateThemeConfig(themeResult.data));
  else if (themeResult.missing) results.push({ level: 'error', file: 'theme_config.json', message: 'Not found. This is the core visual identity config — your app needs it. Create platform/config/theme_config.json.' });

  // Auth is now validated as part of app.json (no separate auth.json)
  // validateAppConfig handles the auth sub-section internally.

  return results;
}

/**
 * Format issues as a styled console group. Called automatically in dev mode.
 */
export function logValidationResults(issues) {
  if (!issues || issues.length === 0) {
    console.log('✅ [CONFIG] All config files validated — no issues found.');
    return;
  }

  const errors = issues.filter(i => i.level === 'error');
  const warns = issues.filter(i => i.level === 'warn');
  const infos = issues.filter(i => i.level === 'info');

  const label = errors.length > 0
    ? `❌ [CONFIG] ${errors.length} error(s), ${warns.length} warning(s)`
    : warns.length > 0
      ? `⚠️ [CONFIG] ${warns.length} warning(s), ${infos.length} info`
      : `ℹ️ [CONFIG] ${infos.length} info message(s)`;

  console.groupCollapsed(label);
  for (const issue of issues) {
    const icon = issue.level === 'error' ? '❌' : issue.level === 'warn' ? '⚠️' : 'ℹ️';
    const method = issue.level === 'error' ? 'error' : issue.level === 'warn' ? 'warn' : 'info';
    console[method](`${icon} [${issue.file}] ${issue.message}`);
  }
  console.groupEnd();
}

export default validateAllConfigs;
