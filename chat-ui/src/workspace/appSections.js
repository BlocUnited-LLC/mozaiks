const pickerHref = key => '/apps?' + new URLSearchParams({ section: key })

// These groups have already passed the mounted route, navigation and role filters.
export function withAppSections(workspaceGroups, appGroups) {
  const sections = []
  const hasDirectory = workspaceGroups.some(group =>
    group.items.some(item => item.path === '/apps' && item.component === 'AppsPage'))
  if (!hasDirectory) return { groups: workspaceGroups, sections }

  const items = []
  for (const group of appGroups) {
    for (const item of group.items) {
      // A parameterized detail or wildcard needs another selection after the app ID.
      const key = /^\/apps\/:appId\/([\w.~-]+(?:\/[\w.~-]+)*)$/.exec(item.path)?.[1]
      if (!key || key.split('/').some(segment => segment === '.' || segment === '..')) continue
      sections.push({ key, label: item.label, route: item.path })
      items.push({ ...item, path: pickerHref(key) })
    }
  }
  const groups = items.length
    ? [...workspaceGroups, { id: 'portfolio-app-sections', label: 'Manage an app', items }]
    : workspaceGroups
  return { groups, sections }
}

export function activeWorkspacePath(location, sections) {
  if (location.pathname !== '/apps' && location.pathname !== '/apps/') return location.pathname
  const keys = new URLSearchParams(location.search).getAll('section')
  return keys.length === 1 && sections.some(section => section.key === keys[0]) ? pickerHref(keys[0]) : '/apps'
}
