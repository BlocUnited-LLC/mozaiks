/**
 * Config Validator
 *
 * Validates the declarative config files (theme_config.json and shell.json)
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
const SHELL_ACTION_SURFACES = new Set(['studio', 'app', 'user', 'workflow_session', 'transition', 'public', 'page']);
const SHELL_MODES = new Set(['standard', 'workspace', 'conversation', 'focused', 'immersive', 'public', 'social']);
const SHELL_ACTION_WHEN_FIELDS = new Set([
  'surface',
  'surfaces',
  'shellMode',
  'shell_mode',
  'routeGroup',
  'route_group',
  'workflow',
  'workflow_id',
  'workflowSequence',
  'workflow_sequence',
  'sequence',
  'transition',
  'transition_id',
  'appLifecycle',
  'app_lifecycle',
  'hasActiveApp',
  'has_active_app',
]);

function isStringOrStringList(value) {
  return (
    typeof value === 'string' ||
    (Array.isArray(value) && value.every((item) => typeof item === 'string' && item.trim()))
  );
}

function validatePath(value, issues, file, field) {
  if (value !== undefined && (typeof value !== 'string' || !value.startsWith('/'))) {
    issues.push({ level: 'error', file, message: `${field} must be a route path starting with "/".` });
  }
}

function validateShellActionWhen(when, issues, file, prefix) {
  if (!when || typeof when !== 'object' || Array.isArray(when)) {
    issues.push({ level: 'error', file, message: `${prefix}.when must be an object with semantic match fields.` });
    return;
  }

  Object.keys(when).forEach((field) => {
    if (!SHELL_ACTION_WHEN_FIELDS.has(field)) {
      issues.push({ level: 'error', file, message: `${prefix}.when.${field} is not supported.` });
    }
  });
  const hasCondition = Object.values(when).some((value) => (
    value !== undefined &&
    value !== null &&
    (!Array.isArray(value) || value.length > 0)
  ));
  if (!hasCondition) {
    issues.push({ level: 'error', file, message: `${prefix}.when must include at least one semantic match field.` });
  }

  const surface = when.surface ?? when.surfaces;
  if (surface !== undefined) {
    const surfaces = Array.isArray(surface) ? surface : [surface];
    surfaces.forEach((item) => {
      if (!SHELL_ACTION_SURFACES.has(item)) {
        issues.push({ level: 'error', file, message: `${prefix}.when.surface must use one of ${Array.from(SHELL_ACTION_SURFACES).join(', ')}.` });
      }
    });
  }

  const shellMode = when.shellMode ?? when.shell_mode;
  if (shellMode !== undefined) {
    const modes = Array.isArray(shellMode) ? shellMode : [shellMode];
    modes.forEach((item) => {
      if (!SHELL_MODES.has(item)) {
        issues.push({ level: 'error', file, message: `${prefix}.when.shellMode must use one of ${Array.from(SHELL_MODES).join(', ')}.` });
      }
    });
  }

  ['routeGroup', 'route_group', 'workflow', 'workflow_id', 'workflowSequence', 'workflow_sequence', 'sequence', 'transition', 'transition_id', 'appLifecycle', 'app_lifecycle'].forEach((field) => {
    if (when[field] !== undefined && !isStringOrStringList(when[field])) {
      issues.push({ level: 'error', file, message: `${prefix}.when.${field} must be a string or list of strings.` });
    }
  });

  ['hasActiveApp', 'has_active_app'].forEach((field) => {
    if (when[field] !== undefined && typeof when[field] !== 'boolean') {
      issues.push({ level: 'error', file, message: `${prefix}.when.${field} must be true or false.` });
    }
  });
}

function validateShellActionVariants(variants, issues, file, prefix) {
  if (variants === undefined) return;
  if (!Array.isArray(variants)) {
    issues.push({ level: 'error', file, message: `${prefix}.variants must be an array.` });
    return;
  }
  variants.forEach((entry, i) => {
    const entryPrefix = `${prefix}.variants[${i}]`;
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      issues.push({ level: 'error', file, message: `${entryPrefix} must be an object.` });
      return;
    }
    validateShellActionWhen(entry.when, issues, file, entryPrefix);
    validatePath(entry.path, issues, file, `${entryPrefix}.path`);
    validatePath(entry.fallbackPath ?? entry.fallback_path, issues, file, `${entryPrefix}.fallbackPath`);
    validatePath(entry.pathTemplate ?? entry.path_template, issues, file, `${entryPrefix}.pathTemplate`);
    if (entry.href !== undefined && (typeof entry.href !== 'string' || entry.href.length === 0)) {
      issues.push({ level: 'error', file, message: `${entryPrefix}.href must be a non-empty string.` });
    }
    if (entry.icon && !ICON_FILE_RE.test(entry.icon) && !URL_RE.test(entry.icon)) {
      issues.push({ level: 'error', file, message: `${entryPrefix}.icon="${entry.icon}" is not a valid asset filename.` });
    }
  });
}

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
        issues.push({ level: 'error', file, message: `assets.logo="${config.assets.logo}" doesn't look like a file. Use a filename like "logo.svg" (placed in brand/assets/ within the active app root).` });
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

  // Chat UI hints
  const ui = config.ui;
  if (ui?.chat?.modes && typeof ui.chat.modes !== 'object') {
    issues.push({ level: 'error', file, message: '"ui.chat.modes" must be an object keyed by chat mode.' });
  }

  return issues;
}

function validateShellConfig(config) {
  const issues = [];
  const file = 'shell.json';

  if (!config || typeof config !== 'object') {
    issues.push({ level: 'error', file, message: 'Shell config is empty or not valid JSON.' });
    return issues;
  }

  if (config.header) {
    const logo = config.header.logo;
    if (logo?.src && !ICON_FILE_RE.test(logo.src) && !URL_RE.test(logo.src)) {
      issues.push({ level: 'error', file, message: `header.logo.src="${logo.src}" is not a valid asset filename. Use a filename like "logo.svg" or a full URL.` });
    }
    if (config.header.pages && !Array.isArray(config.header.pages)) {
      issues.push({ level: 'error', file, message: 'header.pages must be an array of { label, path } items.' });
    } else if (Array.isArray(config.header.pages)) {
      config.header.pages.forEach((page, i) => {
        if (!page?.path || typeof page.path !== 'string' || !page.path.startsWith('/')) {
          issues.push({ level: 'error', file, message: `header.pages[${i}].path must start with "/" so the shell can navigate to it.` });
        }
        if (!page?.label || typeof page.label !== 'string') {
          issues.push({ level: 'warn', file, message: `header.pages[${i}] is missing a human-readable "label".` });
        }
      });
    }
    if (Array.isArray(config.header.actions)) {
      config.header.actions.forEach((action, i) => {
        if (action.icon && !ICON_FILE_RE.test(action.icon) && !URL_RE.test(action.icon)) {
          issues.push({ level: 'error', file, message: `header.actions[${i}].icon="${action.icon}" is not a valid asset filename. Use "sparkle.svg" not "sparkle".` });
        }
        ['route_overrides', 'routeOverrides', 'route_variants', 'routeVariants'].forEach((field) => {
          if (action[field] !== undefined) {
            issues.push({ level: 'error', file, message: `header.actions[${i}].${field} is not supported. Use variants[].when with semantic shell context.` });
          }
        });
        validateShellActionVariants(action.variants, issues, file, `header.actions[${i}]`);
        validatePath(action.path, issues, file, `header.actions[${i}].path`);
        validatePath(action.fallbackPath ?? action.fallback_path, issues, file, `header.actions[${i}].fallbackPath`);
        validatePath(action.pathTemplate ?? action.path_template, issues, file, `header.actions[${i}].pathTemplate`);
        if (action.path_by_role !== undefined) {
          if (!action.path_by_role || typeof action.path_by_role !== 'object' || Array.isArray(action.path_by_role)) {
            issues.push({ level: 'error', file, message: `header.actions[${i}].path_by_role must be an object mapping role names to route paths.` });
          } else {
            Object.entries(action.path_by_role).forEach(([role, path]) => {
              if (typeof path !== 'string' || !path.startsWith('/')) {
                issues.push({ level: 'error', file, message: `header.actions[${i}].path_by_role["${role}"] must be a route path starting with "/".` });
              }
            });
          }
        }
        if (action.href_by_role !== undefined) {
          if (!action.href_by_role || typeof action.href_by_role !== 'object' || Array.isArray(action.href_by_role)) {
            issues.push({ level: 'error', file, message: `header.actions[${i}].href_by_role must be an object mapping role names to URLs/paths.` });
          } else {
            Object.entries(action.href_by_role).forEach(([role, href]) => {
              if (typeof href !== 'string' || href.length === 0) {
                issues.push({ level: 'error', file, message: `header.actions[${i}].href_by_role["${role}"] must be a non-empty string.` });
              }
            });
          }
        }
      });
    }
  }

  if (config.profile) {
    if (config.profile.show !== false && config.profile.icon) {
      if (!ICON_FILE_RE.test(config.profile.icon) && !URL_RE.test(config.profile.icon)) {
        issues.push({ level: 'error', file, message: `profile.icon="${config.profile.icon}" is not a valid asset filename. Use "profile.svg".` });
      }
    }
    if (Array.isArray(config.profile.menu)) {
      config.profile.menu.forEach((item, i) => {
        if (item.type === 'divider') return;
        if (!item.id) {
          issues.push({ level: 'warn', file, message: `profile.menu[${i}] is missing an "id" field. Each menu item needs a unique id.` });
        }
        if (!item.label) {
          issues.push({ level: 'warn', file, message: `profile.menu[${i}] is missing a "label". The menu item won't have visible text.` });
        }
        if (item.icon && !ICON_FILE_RE.test(item.icon) && !URL_RE.test(item.icon)) {
          issues.push({ level: 'error', file, message: `profile.menu[${i}].icon="${item.icon}" is not a valid filename. Use "settings.svg" not "settings".` });
        }
        if (item.action === 'navigate' && !item.href && !item.path) {
          issues.push({ level: 'error', file, message: `profile.menu[${i}] has action="navigate" but no "href" or "path". Where should it navigate to?` });
        }
      });
    }
  }

  if (config.notifications?.show !== false && config.notifications?.icon) {
    if (!ICON_FILE_RE.test(config.notifications.icon) && !URL_RE.test(config.notifications.icon)) {
      issues.push({ level: 'error', file, message: `notifications.icon="${config.notifications.icon}" is not a valid asset filename. Use "notifications.svg".` });
    }
  }

  if (config.footer?.links && !Array.isArray(config.footer.links)) {
    issues.push({ level: 'error', file, message: '"footer.links" must be an array of { label, href } items.' });
  }
  if (config.footer?.hideOnMobile !== undefined && typeof config.footer.hideOnMobile !== 'boolean') {
    issues.push({ level: 'error', file, message: 'footer.hideOnMobile must be true or false.' });
  }
  if (config.footer?.mobileVisible !== undefined && typeof config.footer.mobileVisible !== 'boolean') {
    issues.push({ level: 'error', file, message: 'footer.mobileVisible must be true or false.' });
  }

  const bottomBar = config.mobile?.bottomBar;
  if (bottomBar !== undefined) {
    if (!bottomBar || typeof bottomBar !== 'object' || Array.isArray(bottomBar)) {
      issues.push({ level: 'error', file, message: 'mobile.bottomBar must be an object.' });
    } else {
      if (bottomBar.visible !== undefined && bottomBar.visible !== 'auto' && typeof bottomBar.visible !== 'boolean') {
        issues.push({ level: 'error', file, message: 'mobile.bottomBar.visible must be true, false, or "auto".' });
      }
      if (bottomBar.items !== undefined && !Array.isArray(bottomBar.items)) {
        issues.push({ level: 'error', file, message: 'mobile.bottomBar.items must be an array of menu items.' });
      } else if (Array.isArray(bottomBar.items)) {
        const visibleItems = bottomBar.items.filter((item) => item?.visible !== false);
        if (visibleItems.length > 5) {
          issues.push({ level: 'error', file, message: 'mobile.bottomBar.items may include at most five visible items.' });
        }
        bottomBar.items.forEach((item, i) => {
          if (!item || typeof item !== 'object' || Array.isArray(item)) {
            issues.push({ level: 'error', file, message: `mobile.bottomBar.items[${i}] must be an object.` });
            return;
          }
          if (item.action === 'navigate' && !item.href && !item.path) {
            issues.push({ level: 'error', file, message: `mobile.bottomBar.items[${i}] has action="navigate" but no "href" or "path".` });
          }
        });
      }
    }
  }

  const shortcuts = config.shortcuts;
  if (shortcuts !== undefined) {
    if (!shortcuts || typeof shortcuts !== 'object' || Array.isArray(shortcuts)) {
      issues.push({ level: 'error', file, message: 'shortcuts must be an object.' });
    } else {
      const allowedShortcutKeys = new Set(['header', 'profile', 'mobile', 'footer', 'footerHideOnMobile']);
      Object.keys(shortcuts).forEach((key) => {
        if (!allowedShortcutKeys.has(key)) {
          issues.push({ level: 'error', file, message: `shortcuts.${key} is not supported. Use header, profile, mobile, footer, or footerHideOnMobile.` });
        }
      });
      ['header', 'profile', 'mobile', 'footer'].forEach((key) => {
        if (shortcuts[key] !== undefined && (!Array.isArray(shortcuts[key]) || shortcuts[key].some((item) => typeof item !== 'string'))) {
          issues.push({ level: 'error', file, message: `shortcuts.${key} must be an array of shell primitive ids.` });
        }
      });
      if (shortcuts.footerHideOnMobile !== undefined && typeof shortcuts.footerHideOnMobile !== 'boolean') {
        issues.push({ level: 'error', file, message: 'shortcuts.footerHideOnMobile must be true or false.' });
      }
    }
  }

  const policy = config.navigation?.policy || config.navigation;
  if (policy && typeof policy === 'object' && !Array.isArray(policy)) {
    if (policy.max_mobile_items !== undefined) {
      issues.push({ level: 'error', file, message: 'navigation.max_mobile_items is not supported. Use navigation.policy.maxMobileItems.' });
    }
    if (policy.auto_from_pages !== undefined) {
      issues.push({ level: 'error', file, message: 'navigation.auto_from_pages is not supported. Use navigation.policy.autoFromPages.' });
    }
    if (policy.maxMobileItems !== undefined && (!Number.isInteger(policy.maxMobileItems) || policy.maxMobileItems < 1 || policy.maxMobileItems > 5)) {
      issues.push({ level: 'error', file, message: 'navigation.policy.maxMobileItems must be an integer from 1 to 5.' });
    }
    if (policy.autoFromPages !== undefined && typeof policy.autoFromPages !== 'boolean') {
      issues.push({ level: 'error', file, message: 'navigation.policy.autoFromPages must be true or false.' });
    }
  }

  const chrome = config.chrome;
  if (chrome !== undefined) {
    if (!chrome || typeof chrome !== 'object' || Array.isArray(chrome)) {
      issues.push({ level: 'error', file, message: 'chrome must be an object.' });
    } else {
      if (chrome.default_mode !== undefined) {
        issues.push({ level: 'error', file, message: 'chrome.default_mode is not supported. Use chrome.defaultMode.' });
      }
      const modes = chrome.modes;
      if (modes !== undefined && (!modes || typeof modes !== 'object' || Array.isArray(modes))) {
        issues.push({ level: 'error', file, message: 'chrome.modes must be an object keyed by shell mode.' });
      } else if (modes) {
        Object.entries(modes).forEach(([mode, modePolicy]) => {
          if (!modePolicy || typeof modePolicy !== 'object' || Array.isArray(modePolicy)) return;
          ['topBar', 'top_bar', 'bottom_bar', 'local_nav'].forEach((field) => {
            if (modePolicy[field] !== undefined) {
              issues.push({ level: 'error', file, message: `chrome.modes.${mode}.${field} is not supported. Use header, bottomBar, or localNav.` });
            }
          });
          ['desktop', 'mobile'].forEach((viewport) => {
            const viewportPolicy = modePolicy[viewport];
            if (!viewportPolicy || typeof viewportPolicy !== 'object' || Array.isArray(viewportPolicy)) return;
            ['topBar', 'top_bar', 'bottom_bar', 'local_nav'].forEach((field) => {
              if (viewportPolicy[field] !== undefined) {
                issues.push({ level: 'error', file, message: `chrome.modes.${mode}.${viewport}.${field} is not supported. Use header, bottomBar, or localNav.` });
              }
            });
          });
        });
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
      issues.push({ level: 'info', file, message: 'No admin users declared. Add app.json -> admins to make admin bootstrap easier.' });
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
  else if (themeResult.missing) results.push({ level: 'error', file: 'theme_config.json', message: 'Not found. This is the core visual identity config — your app needs it. Create brand/theme_config.json.' });

  const shellResult = await safeLoad(
    baseUrl ? `${baseUrl}/api/shell-config` : '/api/shell-config',
    'shell.json',
  );

  if (shellResult.data) results.push(...validateShellConfig(shellResult.data));
  else if (shellResult.missing) results.push({ level: 'info', file: 'shell.json', message: 'Not found. The app will fall back to the default shell chrome.' });

  return results;
}

/**
 * Format issues as a styled console group. Called automatically in dev mode.
 */
export function logValidationResults(issues) {
  if (!issues || issues.length === 0) {
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
