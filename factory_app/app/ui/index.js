/**
 * factory_app/app/ui — first-party workspace UI registration barrel.
 *
 * User-facing app pages (declarative schemas, custom app routes) live here.
 * Studio admin pages are co-located with the admin portal under app/admin/
 * and registered via registerAdminComponents from that module.
 */

import { lazy } from 'react'
import { registerAdminComponents } from '../admin/index.js'
import MessagingProfilePanel from './components/MessagingProfilePanel.jsx'
import ContactsProfilePanel from './components/ContactsProfilePanel.jsx'
import MessagingTab from './components/MessagingTab.jsx'
import ContactsTab from './components/ContactsTab.jsx'

const MessagesPage = lazy(() => import('./pages/custom/MessagesPage.jsx'))
const MessageThreadPage = lazy(() => import('./pages/custom/MessageThreadPage.jsx'))

export function register(registerComponent) {
  registerAdminComponents(registerComponent)

  registerComponent('MessagesPage', MessagesPage, {
    description: 'Messages — demo DM inbox for testing the messaging UI in Studio.',
  })

  registerComponent('MessageThreadPage', MessageThreadPage, {
    description: 'Message thread — demo conversation view for testing messaging UI in Studio.',
  })

  registerComponent('MessagingProfilePanel', MessagingProfilePanel, {
    description: 'Messaging summary — unread count and link to /messages, injected by the messages module profile panel.',
  })

  registerComponent('ContactsProfilePanel', ContactsProfilePanel, {
    description: 'Contacts list — friends roster with message buttons, injected by the contacts module profile panel.',
  })

  registerComponent('MessagingTab', MessagingTab, {
    description: 'Messaging tab — thread list with unread badges, injected by the messages module profile tab.',
  })

  registerComponent('ContactsTab', ContactsTab, {
    description: 'Contacts tab — friends roster with message buttons, injected by the contacts module profile tab.',
  })
}
