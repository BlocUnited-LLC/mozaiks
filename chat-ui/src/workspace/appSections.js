// These groups have already passed the mounted route, navigation and role filters.
export function withAppSections(workspaceGroups, appGroups) {
  const sections = []
  const hasDirectory = workspaceGroups.some(group =>
    group.items.some(item => item.path === '/apps' && item.component === 'AppsPage'))
  if (!hasDirectory) return { groups: workspaceGroups, sections }

  for (const group of appGroups) {
    for (const item of group.items) {
      // A parameterized detail or wildcard needs another selection after the app ID.
      const key = /^\/apps\/:appId\/([\w.~-]+(?:\/[\w.~-]+)*)$/.exec(item.path)?.[1]
      if (!key || key.split('/').some(segment => segment === '.' || segment === '..')) continue
      sections.push({ key, label: item.label, route: item.path })
    }
  }
  // Preserve existing app-picker deep links without projecting app tools into the global menu.
  return { groups: workspaceGroups, sections }
}

export function activeWorkspacePath(location) {
  return location.pathname === '/apps/' ? '/apps' : location.pathname
}
