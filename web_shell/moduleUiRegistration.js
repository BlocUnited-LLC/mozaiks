/**
 * Register the components module UI barrels export.
 *
 * Each export registers as `<module_id>:<Export>` and as `<Export>`. Runs after
 * the app's own registration barrel and never overrides: a component the app
 * registered under the same name keeps it, so an app customizes an installed
 * library's page by registering its own.
 */
export function registerModuleUi(modules, registry) {
  for (const [moduleId, barrel] of Object.entries(modules || {})) {
    for (const [name, component] of Object.entries(barrel || {})) {
      if (name === 'default' || component == null || !['function', 'object'].includes(typeof component)) continue;
      const namespaced = `${moduleId}:${name}`;
      if (!registry.hasComponent(namespaced)) {
        registry.registerComponent(namespaced, component, { description: `${moduleId} module component (namespaced)` });
      }
      if (!registry.hasComponent(name)) {
        registry.registerComponent(name, component, { description: `${moduleId} module component` });
      }
    }
  }
}
