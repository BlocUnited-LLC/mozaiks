/**
 * Config Validator
 *
 * Validates all declarative JSON config files at startup and surfaces
 * human-readable errors. Designed for non-technical founders — messages
 * explain WHAT is wrong and HOW to fix it.
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
// Individual file validators
// ---------------------------------------------------------------------------

function validateBrand(brand) {
  const issues = [];
  const file = 'brand.json';

  if (!brand || typeof brand !== 'object') {
    issues.push({ level: 'error', file, message: 'File is empty or not valid JSON.' });
    return issues;
  }

  // Name
  if (!brand.name) {
    issues.push({ level: 'warn', file, message: 'Missing "name". Your app will show as "App" in the browser tab.' });
  }

  // Assets
  if (!brand.assets) {
    issues.push({ level: 'error', file, message: 'Missing "assets" block. Add at least: { "assets": { "logo": "your-logo.svg" } }' });
  } else {
    if (!brand.assets.logo) {
      issues.push({ level: 'warn', file, message: 'Missing "assets.logo". The header will have no logo image.' });
    } else if (!ICON_FILE_RE.test(brand.assets.logo) && !URL_RE.test(brand.assets.logo)) {
      issues.push({ level: 'error', file, message: `assets.logo="${brand.assets.logo}" doesn't look like a file. Use a filename like "logo.svg" (placed in brand/public/assets/).` });
    }
    if (!brand.assets.backgroundImage) {
      issues.push({ level: 'info', file, message: 'No "assets.backgroundImage" set. The chat background will be a solid color.' });
    }
  }

  // Colors
  if (!brand.colors) {
    issues.push({ level: 'warn', file, message: 'Missing "colors" block. The default blue/indigo palette will be used.' });
  } else {
    const requiredColors = ['primary', 'secondary'];
    for (const name of requiredColors) {
      const c = brand.colors[name];
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
  if (!brand.fonts) {
    issues.push({ level: 'info', file, message: 'No "fonts" block. System fonts will be used.' });
  }

  return issues;
}

function validateUI(ui) {
  const issues = [];
  const file = 'ui.json';

  if (!ui || typeof ui !== 'object') {
    issues.push({ level: 'warn', file, message: 'File is empty or missing. Default header/profile/footer will be used.' });
    return issues;
  }

  // Header
  if (ui.header) {
    const logo = ui.header.logo;
    if (logo) {
      if (logo.src && !ICON_FILE_RE.test(logo.src) && !URL_RE.test(logo.src)) {
        issues.push({ level: 'error', file, message: `header.logo.src="${logo.src}" is not a valid asset filename. Use a filename like "logo.svg" or a full URL.` });
      }
    }

    if (Array.isArray(ui.header.actions)) {
      ui.header.actions.forEach((action, i) => {
        if (action.icon && !ICON_FILE_RE.test(action.icon) && !URL_RE.test(action.icon)) {
          issues.push({ level: 'error', file, message: `header.actions[${i}].icon="${action.icon}" is not a valid asset filename. Use "sparkle.svg" not "sparkle".` });
        }
      });
    }
  }

  // Profile
  if (ui.profile) {
    if (ui.profile.show !== false && ui.profile.icon) {
      if (!ICON_FILE_RE.test(ui.profile.icon) && !URL_RE.test(ui.profile.icon)) {
        issues.push({ level: 'error', file, message: `profile.icon="${ui.profile.icon}" is not a valid asset filename. Use "profile.svg" (placed in brand/public/assets/).` });
      }
    }
    if (Array.isArray(ui.profile.menu)) {
      ui.profile.menu.forEach((item, i) => {
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
          issues.push({ level: 'error', file, message: `profile.menu[${i}] has action="navigate" but no "href". Where should it navigate to?` });
        }
      });
    }
  }

  // Notifications
  if (ui.notifications?.show !== false && ui.notifications?.icon) {
    if (!ICON_FILE_RE.test(ui.notifications.icon) && !URL_RE.test(ui.notifications.icon)) {
      issues.push({ level: 'error', file, message: `notifications.icon="${ui.notifications.icon}" is not a valid asset filename. Use "notifications.svg".` });
    }
  }

  return issues;
}

function validateNavigation(nav) {
  const issues = [];
  const file = 'navigation.json';

  if (!nav || typeof nav !== 'object') {
    issues.push({ level: 'warn', file, message: 'File is empty or missing. Default navigation will be used.' });
    return issues;
  }

  if (!nav.version) {
    issues.push({ level: 'info', file, message: 'Missing "version" field. Consider adding "version": "1.0.0".' });
  }

  // landing_spot
  if (nav.landing_spot && typeof nav.landing_spot === 'string') {
    if (!nav.landing_spot.startsWith('/')) {
      issues.push({ level: 'error', file, message: `landing_spot="${nav.landing_spot}" must start with "/". Example: "/" or "/dashboard".` });
    }
  }

  // Pages (canonical flat array)
  const pages = Array.isArray(nav.pages) ? nav.pages : [];
  const CORE_PATHS = ['/', '/chat', '/admin'];
  pages.forEach((page, i) => {
    if (!page.path && !page.href && !page.trigger) {
      issues.push({ level: 'error', file, message: `pages[${i}] has no "path", "href", or "trigger". Every page needs at least one navigation target.` });
    }
    if (!page.label && !page.title) {
      issues.push({ level: 'warn', file, message: `pages[${i}] has no "label". It will show as "${page.id || page.path || 'unnamed'}" in the Discover menu.` });
    }
    if (page.path && CORE_PATHS.includes(page.path)) {
      issues.push({ level: 'warn', file, message: `pages[${i}].path="${page.path}" is a core route — it's already built-in and will be ignored here.` });
    }
    if (page.icon && !ICON_FILE_RE.test(page.icon) && !URL_RE.test(page.icon)) {
      issues.push({ level: 'warn', file, message: `pages[${i}].icon="${page.icon}" is not a valid asset filename. Use "icon-name.svg" not "icon-name".` });
    }
  });

  return issues;
}

function validateAuth(auth) {
  const issues = [];
  const file = 'auth.json';

  if (!auth || typeof auth !== 'object') {
    issues.push({ level: 'warn', file, message: 'File is empty or missing. Auth will use defaults (Keycloak on localhost:8080).' });
    return issues;
  }

  if (!auth.provider) {
    issues.push({ level: 'info', file, message: 'No "provider" set. Defaulting to "keycloak".' });
  }

  if (auth.provider === 'keycloak' || !auth.provider) {
    const kc = auth.keycloak;
    if (!kc) {
      issues.push({ level: 'warn', file, message: 'Missing "keycloak" block. Auth server defaults (localhost:8080/mozaiks) will be used.' });
    } else {
      if (!kc.authority || !URL_RE.test(kc.authority)) {
        issues.push({ level: 'warn', file, message: `keycloak.authority="${kc.authority || ''}" — make sure this points to your Keycloak server URL.` });
      }
      if (!kc.realm) {
        issues.push({ level: 'warn', file, message: 'keycloak.realm is missing. Defaulting to "mozaiks".' });
      }
      if (!kc.clientId) {
        issues.push({ level: 'warn', file, message: 'keycloak.clientId is missing. Defaulting to "mozaiks-app".' });
      }
    }
  }

  // Roles
  if (auth.roles) {
    if (!auth.roles.admin) {
      issues.push({ level: 'info', file, message: 'roles.admin not set. The admin role name defaults to "admin".' });
    }
    if (!Array.isArray(auth.roles.adminEmails) || auth.roles.adminEmails.length === 0) {
      issues.push({ level: 'info', file, message: 'roles.adminEmails is empty. No user will have admin access to the Admin Portal.' });
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
  if (!app.appId) {
    issues.push({ level: 'warn', file, message: 'Missing "appId". A default app ID will be used.' });
  }
  if (!app.apiUrl) {
    issues.push({ level: 'error', file, message: 'Missing "apiUrl". The app won\'t be able to connect to the backend. Example: "http://localhost:8000".' });
  }
  if (!app.wsUrl) {
    issues.push({ level: 'warn', file, message: 'Missing "wsUrl". WebSocket connections will fall back to the apiUrl.' });
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

  // Load all configs in parallel
  const [brand, ui, nav, auth] = await Promise.all([
    safeLoad('/brand.json', 'brand.json'),
    safeLoad('/ui.json', 'ui.json'),
    safeLoad('/navigation.json', 'navigation.json'),
    safeLoad('/auth.json', 'auth.json'),
  ]);

  if (brand.data) results.push(...validateBrand(brand.data));
  else if (brand.missing) results.push({ level: 'error', file: 'brand.json', message: 'File not found. This is the core visual identity file — your app needs it. Create brand/public/brand.json.' });

  if (ui.data) results.push(...validateUI(ui.data));
  else if (ui.missing) results.push({ level: 'info', file: 'ui.json', message: 'Not found — default header, profile, and footer will be used.' });

  if (nav.data) results.push(...validateNavigation(nav.data));
  else if (nav.missing) results.push({ level: 'info', file: 'navigation.json', message: 'Not found — the core shell (Chat + Admin) will load with no extra pages.' });

  if (auth.data) results.push(...validateAuth(auth.data));
  else if (auth.missing) results.push({ level: 'info', file: 'auth.json', message: 'Not found — mock auth will be used for development.' });

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
