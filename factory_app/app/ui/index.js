/**
 * factory_app/app/ui — first-party workspace UI registration barrel.
 *
 * User-facing app pages (declarative schemas, custom app routes) live here.
 * Studio admin pages are co-located with the admin portal under app/admin/
 * and registered via registerAdminComponents from that module.
 *
 * Social profile tab components are registered here so ProfilePage can
 * resolve FriendListTab, ActivityFeedTab, and UserPostsTab from the
 * component registry when hydrating profile.yaml tab declarations.
 */

import { registerAdminComponents } from '../admin/index.js'
import { FriendListTab, ActivityFeedTab, UserPostsTab } from './components/SocialProfileTabs.jsx'

export function register(registerComponent) {
  registerAdminComponents(registerComponent)

  // Social pack profile tab components
  registerComponent('FriendListTab', FriendListTab)
  registerComponent('ActivityFeedTab', ActivityFeedTab)
  registerComponent('UserPostsTab', UserPostsTab)
}
