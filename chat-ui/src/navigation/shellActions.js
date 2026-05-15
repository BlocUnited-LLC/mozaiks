const TEMPLATE_TOKEN_RE = /\{([A-Za-z0-9_]+)\}/g;

const normalizeList = (value) => {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string' && item.trim());
  if (typeof value === 'string' && value.trim()) return [value.trim()];
  return [];
};

const getRouteMeta = (route) => (route?.meta && typeof route.meta === 'object' ? route.meta : {});

const getRouteNavigation = (route) => {
  const meta = getRouteMeta(route);
  if (route?.navigation && typeof route.navigation === 'object') return route.navigation;
  if (meta.navigation && typeof meta.navigation === 'object') return meta.navigation;
  return {};
};

const getShellMode = (route, explicitMode) => {
  const meta = getRouteMeta(route);
  return (
    explicitMode ||
    meta.shellMode ||
    meta.shell_mode ||
    route?.shellMode ||
    route?.shell_mode ||
    null
  );
};

const getUserRoles = (user) => {
  if (!user) return [];
  if (Array.isArray(user.roles)) return user.roles;
  if (Array.isArray(user.role)) return user.role;
  if (typeof user.role === 'string') return [user.role];
  return [];
};

const firstSearchValue = (searchParams, keys) => {
  for (const key of keys) {
    const value = searchParams.get(key);
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
};

const appIdFromPath = (pathname) => {
  const match = /^\/apps\/([^/?#]+)/.exec(pathname || '');
  if (!match) return null;
  const value = decodeURIComponent(match[1]);
  if (!value || value === 'new') return null;
  return value;
};

const deriveSurface = ({ pathname, searchParams, route, shellMode }) => {
  const navigation = getRouteNavigation(route);
  const group = navigation.group || null;
  const hasWorkflowSearch = searchParams.has('workflow') || searchParams.has('workflow_id');

  if (pathname === '/' && hasWorkflowSearch) return 'workflow_session';
  if (pathname === '/chat' || pathname.startsWith('/chat/') || pathname === '/app' || pathname.startsWith('/app/')) {
    return 'workflow_session';
  }
  if (group === 'app-console' || (pathname.startsWith('/apps/') && pathname !== '/apps/new')) return 'app_console';
  if (group === 'workspace-console' || pathname === '/apps') return 'console';
  if (route?.transition || route?.sequence) return 'transition';
  if (shellMode === 'conversation') return 'workflow_session';
  if (shellMode === 'public') return 'public';
  return 'page';
};

export function deriveShellActionContext({
  location,
  route = null,
  shellMode = null,
  user = null,
  roles = null,
} = {}) {
  const pathname = location?.pathname || '/';
  const searchParams = new URLSearchParams(location?.search || '');
  const resolvedShellMode = getShellMode(route, shellMode);
  const appId = firstSearchValue(searchParams, ['appId', 'app_id']) || appIdFromPath(pathname);
  const workflow = firstSearchValue(searchParams, ['workflow', 'workflow_id']) || route?.workflow || route?.workflow_id || null;
  const workflowSequence = firstSearchValue(searchParams, ['sequence', 'workflow_sequence'])
    || route?.sequence
    || route?.workflow_sequence
    || null;
  const transition = firstSearchValue(searchParams, ['transition', 'transition_id']) || route?.transition || route?.transition_id || null;

  return {
    pathname,
    shellMode: resolvedShellMode,
    surface: deriveSurface({ pathname, searchParams, route, shellMode: resolvedShellMode }),
    routeGroup: getRouteNavigation(route).group || null,
    workflow,
    workflowSequence,
    transition,
    appId,
    hasActiveApp: Boolean(appId),
    roles: Array.isArray(roles) ? roles : getUserRoles(user),
  };
}

const matchString = (actual, expected) => {
  if (expected === undefined || expected === null) return true;
  const values = normalizeList(expected);
  if (values.length === 0) return true;
  return values.includes(String(actual || ''));
};

const matchBoolean = (actual, expected) => {
  if (expected === undefined || expected === null) return true;
  return Boolean(actual) === Boolean(expected);
};

const variantMatches = (variant, context) => {
  if (!variant || typeof variant !== 'object' || Array.isArray(variant)) return false;
  const when = variant.when;
  if (!when || typeof when !== 'object' || Array.isArray(when)) return false;
  const hasCondition = Object.values(when).some((value) => (
    value !== undefined &&
    value !== null &&
    (!Array.isArray(value) || value.length > 0)
  ));
  if (!hasCondition) return false;

  return (
    matchString(context.surface, when.surface ?? when.surfaces) &&
    matchString(context.shellMode, when.shellMode ?? when.shell_mode) &&
    matchString(context.routeGroup, when.routeGroup ?? when.route_group) &&
    matchString(context.workflow, when.workflow ?? when.workflow_id) &&
    matchString(context.workflowSequence, when.workflowSequence ?? when.workflow_sequence ?? when.sequence) &&
    matchString(context.transition, when.transition ?? when.transition_id) &&
    matchString(context.appLifecycle, when.appLifecycle ?? when.app_lifecycle) &&
    matchBoolean(context.hasActiveApp, when.hasActiveApp ?? when.has_active_app)
  );
};

const resolveRoleScopedRoute = (mapping, roles = []) => {
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) return null;

  for (const role of roles) {
    const candidate = mapping[role];
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }

  const fallback = mapping.default ?? mapping['*'];
  if (typeof fallback === 'string' && fallback.trim()) return fallback.trim();
  return null;
};

const interpolatePathTemplate = (template, context) => {
  if (typeof template !== 'string' || !template.trim()) return null;
  const values = {
    appId: context.appId,
    app_id: context.appId,
    workflow: context.workflow,
    workflow_id: context.workflow,
    workflowSequence: context.workflowSequence,
    workflow_sequence: context.workflowSequence,
    sequence: context.workflowSequence,
    transition: context.transition,
    transition_id: context.transition,
  };

  let unresolved = false;
  const resolved = template.replace(TEMPLATE_TOKEN_RE, (_, key) => {
    const value = values[key];
    if (value === undefined || value === null || value === '') {
      unresolved = true;
      return '';
    }
    return encodeURIComponent(String(value));
  });

  if (unresolved || !resolved.startsWith('/')) return null;
  return resolved;
};

const applyPathTemplate = (action, context) => {
  const template = action.pathTemplate || action.path_template;
  const resolvedPath = interpolatePathTemplate(template, context);
  if (resolvedPath) {
    return { ...action, path: resolvedPath };
  }

  const fallbackPath = action.fallbackPath || action.fallback_path;
  if (typeof fallbackPath === 'string' && fallbackPath.startsWith('/')) {
    return { ...action, path: fallbackPath };
  }

  return action;
};

const resolveActionByRole = (action, roles = []) => {
  if (!action || typeof action !== 'object') return action;

  const resolved = { ...action };
  const routeOverride = resolveRoleScopedRoute(action.path_by_role || action.pathByRole, roles);
  const hrefOverride = resolveRoleScopedRoute(action.href_by_role || action.hrefByRole, roles);
  const chosen = routeOverride || hrefOverride;

  if (!chosen) return resolved;
  if (chosen.startsWith('/')) {
    resolved.path = chosen;
    delete resolved.href;
    return resolved;
  }

  resolved.href = chosen;
  delete resolved.path;
  return resolved;
};

export function resolveShellAction(action, context = {}) {
  if (!action || typeof action !== 'object') return action;

  let resolved = { ...action };
  const variants = Array.isArray(action.variants) ? action.variants : [];
  const matched = variants.find((variant) => variantMatches(variant, context));

  if (matched) {
    const override = { ...matched };
    delete override.when;
    resolved = { ...resolved, ...override, id: override.id || resolved.id };
    if (override.path || override.href || override.trigger || override.pathTemplate || override.path_template) {
      delete resolved.path_by_role;
      delete resolved.pathByRole;
      delete resolved.href_by_role;
      delete resolved.hrefByRole;
    }
  }

  resolved = applyPathTemplate(resolved, context);
  return resolveActionByRole(resolved, context.roles || []);
}

export function resolveShellActions(actions, context = {}) {
  if (!Array.isArray(actions)) return [];
  return actions
    .map((action) => resolveShellAction(action, context))
    .filter((action) => action?.visible !== false);
}

export function getNavigationTargetKey(item) {
  if (!item || typeof item !== 'object') return null;

  const path = typeof item.path === 'string' && item.path.trim() ? item.path.trim() : null;
  if (path) return `path:${path}`;

  const href = typeof item.href === 'string' && item.href.trim() ? item.href.trim() : null;
  if (href) return `href:${href}`;

  const trigger = typeof item.trigger === 'string' && item.trigger.trim() ? item.trigger.trim() : null;
  if (trigger) return `trigger:${trigger}`;

  if (item.trigger && typeof item.trigger === 'object') {
    const workflow = item.trigger.workflow || item.trigger.workflow_id || '';
    return workflow ? `workflow:${workflow}` : `trigger:${item.id || 'object'}`;
  }

  return null;
}

export function findHeaderAction(headerConfig, actionId) {
  if (!headerConfig || typeof headerConfig !== 'object') return null;
  if (!Array.isArray(headerConfig.actions)) return null;
  if (typeof actionId !== 'string' || !actionId.trim()) return null;

  return (
    headerConfig.actions.find((action) => action && action.id === actionId) || null
  );
}

export { getUserRoles };

export default findHeaderAction;
