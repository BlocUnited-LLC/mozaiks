/** Core chat fallbacks and declared app routes share one selection policy. */
const CORE_ROUTES = [
  { path: '/', component: 'ChatPage', meta: { title: 'Chat', requiresAuth: true } },
  { path: '/chat/*', component: 'ChatPage', meta: { title: 'Chat', requiresAuth: true } },
  { path: '/app/*', component: 'ChatPage', meta: { title: 'Chat', requiresAuth: true } },
];

export function getShellRoutes(pages) {
  const routablePages = (pages || []).filter(page => page.path && (page.component || page.transition || page.workflow));
  const hasDeclaredRoot = routablePages.some(page => page.path === '/');
  const coreRoutes = CORE_ROUTES.filter(route => route.path !== '/' || !hasDeclaredRoot);
  const corePaths = new Set(coreRoutes.map(route => route.path));
  return {
    coreRoutes,
    pageRoutes: routablePages.filter(page => !corePaths.has(page.path)),
  };
}
