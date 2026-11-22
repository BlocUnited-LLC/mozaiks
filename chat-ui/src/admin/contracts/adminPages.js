/**
 * Standard admin pages — the frontend copy of the catalog owned by
 * `mozaiksai/core/admin/contract.py` (`STANDARD_ADMIN_PAGES`).
 *
 * An app's portal shows only the pages its `admin/admin_registry.yaml` declares.
 * This catalog supplies the page description and empty-state wording for the
 * standard ids, and is the section vocabulary for app-backend panels. A registry
 * may also declare its own page ids; those use the registry label.
 *
 * `tests/test_admin_page_catalog.py` keeps the ids and labels equal to the
 * Python catalog.
 */

export const STANDARD_ADMIN_PAGES = Object.freeze([
  Object.freeze({
    id: 'overview',
    label: 'Overview',
    description: 'How your app is doing right now.',
  }),
  Object.freeze({
    id: 'access',
    label: 'Access',
    description: 'Who can use your app and what they are allowed to do.',
    // The framework lists no accounts itself; say where they live.
    emptyHint: "Accounts are created and managed by this app's sign-in provider.",
  }),
  Object.freeze({
    id: 'usage',
    label: 'Usage',
    description: 'How much AI your app has used and what each run cost.',
  }),
  Object.freeze({
    id: 'billing',
    label: 'Billing',
    description: 'Plans, subscriptions, and payments.',
  }),
  Object.freeze({
    id: 'activity',
    label: 'Activity',
    description: 'A record of what has happened in your app.',
  }),
  Object.freeze({
    id: 'operations',
    label: 'Operations',
    description: 'What is running now and what ran recently.',
  }),
  Object.freeze({
    id: 'settings',
    label: 'Settings',
    description: "This app's details and the settings its features provide.",
  }),
  Object.freeze({
    id: 'integrations',
    label: 'Integrations',
    description: 'Outside services connected to your app.',
  }),
  Object.freeze({
    id: 'support',
    label: 'Support',
    description: 'Requests and questions from the people who use your app.',
  }),
]);

export const STANDARD_ADMIN_PAGE_IDS = Object.freeze(STANDARD_ADMIN_PAGES.map((page) => page.id));

const PAGE_BY_ID = new Map(STANDARD_ADMIN_PAGES.map((page) => [page.id, page]));

export function getStandardAdminPage(pageId) {
  return PAGE_BY_ID.get(pageId) || null;
}
