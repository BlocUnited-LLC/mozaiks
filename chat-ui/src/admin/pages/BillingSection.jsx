import { SectionFrame, EmptyState, AdminExtensionPanels } from '../components/AdminPrimitives.jsx'
import { AppAdminPanels } from '../../pages/AppAdminDashboard.jsx'

export function BillingSection({ extensionPanels }) {
  return (
    <SectionFrame
      title="Billing"
      description="Plans, subscriptions, payment status, invoices, and revenue controls."
    >
      <AppAdminPanels
        embedded
        section="billing"
        showNoBackend={false}
        emptyState={<EmptyState>Billing is not enabled for this app yet.</EmptyState>}
      />
      <AdminExtensionPanels panels={extensionPanels} />
    </SectionFrame>
  )
}
