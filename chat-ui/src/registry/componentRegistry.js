/**
 * Component Registry
 *
 * Central registry for mapping component names (from navigation.json) to React components.
 * Platform and workflow UI packages can register additional components at runtime.
 *
 * @module @mozaiks/chat-ui/registry
 */

const registry = new Map();

/**
 * Register a component in the registry
 * @param {string} name - Component name (used in navigation.json)
 * @param {React.ComponentType} component - React component
 * @param {Object} options - Optional metadata
 * @param {boolean} options.core - Whether this is a core component
 * @param {string} options.description - Component description
 */
export function registerComponent(name, component, options = {}) {
  if (registry.has(name) && !options.override) {
    console.warn(`[ComponentRegistry] Component "${name}" already registered. Use override: true to replace.`);
    return;
  }

  registry.set(name, {
    component,
    core: options.core || false,
    description: options.description || '',
    registeredAt: Date.now()
  });

  if (process.env.NODE_ENV === 'development') {
    console.log(`[ComponentRegistry] Registered: ${name}`, options.core ? '(core)' : '');
  }
}

/**
 * Get a component from the registry
 * @param {string} name - Component name
 * @returns {React.ComponentType|null} The component or null if not found
 */
export function getComponent(name) {
  const entry = registry.get(name);
  return entry?.component || null;
}

/**
 * Check if a component is registered
 * @param {string} name - Component name
 * @returns {boolean}
 */
export function hasComponent(name) {
  return registry.has(name);
}

/**
 * Get all registered component names
 * @returns {string[]}
 */
export function getRegisteredComponents() {
  return Array.from(registry.keys());
}

/**
 * Get component metadata
 * @param {string} name - Component name
 * @returns {Object|null}
 */
export function getComponentMeta(name) {
  const entry = registry.get(name);
  if (!entry) return null;

  const { component, ...meta } = entry;
  return meta;
}

/**
 * Unregister a component (useful for hot reload / testing)
 * @param {string} name - Component name
 */
export function unregisterComponent(name) {
  registry.delete(name);
}

/**
 * Clear all registered components (useful for testing)
 */
export function clearRegistry() {
  registry.clear();
}

export default {
  registerComponent,
  getComponent,
  hasComponent,
  getRegisteredComponents,
  getComponentMeta,
  unregisterComponent,
  clearRegistry
};
