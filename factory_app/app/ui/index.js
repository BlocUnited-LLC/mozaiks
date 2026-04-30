/**
 * factory_app/app/ui — Factory app extension registration.
 *
 * The factory workspace currently owns shared builder workflows and Studio
 * control-plane surfaces. It does not yet expose app-owned custom routes
 * through the standard platform app registry.
 */

export function register(registerComponent) {
  if (typeof registerComponent !== 'function') return;
}