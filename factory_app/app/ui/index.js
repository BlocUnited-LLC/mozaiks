/**
 * factory_app/app/ui — first-party workspace UI registration barrel.
 *
 * User-facing app pages (declarative schemas, custom app routes) live here.
 * Studio admin pages are co-located with the admin portal under app/admin/
 * and registered via registerAdminComponents from that module.
 */

import { registerAdminComponents } from '../admin/index.js'

export function register(registerComponent) {
  registerAdminComponents(registerComponent)
}
