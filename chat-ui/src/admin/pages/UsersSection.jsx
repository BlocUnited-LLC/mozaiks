import { SectionFrame, EmptyState, AdminExtensionPanels } from '../components/AdminPrimitives.jsx'
import { AppAdminPanels } from '../../pages/AppAdminDashboard.jsx'

export function UsersSection({ extensionPanels }) {
  return (
    <SectionFrame
      title="Access"
      description="Members, roles, permissions, and account-level access controls."
    >
      <AppAdminPanels
        embedded
        section="access"
        showNoBackend={false}
        emptyState={
          <EmptyState>No access management backend is connected for this app yet.</EmptyState>
        }
      />
      <AdminExtensionPanels panels={extensionPanels} />
    </SectionFrame>
  )
}
