// ==============================================================================
// FILE: platform/modules/admin_portal/ui/index.js
// DESCRIPTION: Admin Portal module UI entrypoint.
//              Exports the AdminPortal full-page component so @modules
//              auto-discovery can register it in the component registry.
//
// The component name 'AdminPortal' must match the 'component' field in
// platform/modules/admin_portal/module.json navigation block and in
// platform/config/navigation_config.json modules[].
// ==============================================================================

import AdminPortal from './AdminPortal';

const AdminPortalComponents = {
  AdminPortal,
};

export default AdminPortalComponents;
