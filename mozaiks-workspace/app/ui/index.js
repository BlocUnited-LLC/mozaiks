import { registerAdminComponents } from '@mozaiks/factory-admin'

export function register(registerComponent) {
  // Studio management components — always registered so the /apps dashboard
  // and admin portal are available when running in Studio mode.
  registerAdminComponents(registerComponent)

  // Register app-specific custom React surfaces here when declarative config
  // is not enough. Use app/ui/pages/custom/ for full-page custom routes.
}
