/**
 * Local-navigation sectioning.
 *
 * A studio sidebar is a flat list until its pages say otherwise. Any page whose
 * route-manifest `navigation` block declares a `section` string is gathered
 * under that heading; pages without one stay in a single leading unlabeled
 * group, so a manifest that declares no sections renders exactly as it did
 * before this existed.
 *
 * Section sequence is derived from the lowest `order` among a section's own
 * items rather than a separate section-order field — one ordering source, so
 * the two cannot drift apart.
 *
 * @module @mozaiks/chat-ui/navigation/navSections
 */

/**
 * Read the section heading a page declares, if any.
 *
 * @param {object} navigation - A page's resolved `navigation` metadata.
 * @returns {string|null} The trimmed section label, or null when unsectioned.
 */
export function resolveSectionLabel(navigation) {
  const raw = navigation?.section;
  const trimmed = typeof raw === 'string' ? raw.trim() : '';
  return trimmed || null;
}

/**
 * Split ordered nav items into `{label, items}` groups for the sidebar.
 *
 * @param {Array<object>} items - Nav items, already sorted by `order`.
 * @returns {Array<{label: string|null, items: Array<object>}>} Render groups.
 */
export function groupItemsIntoSections(items) {
  const ordered = Array.isArray(items) ? items : [];
  const unsectioned = [];
  const sections = new Map();

  for (const item of ordered) {
    const label = resolveSectionLabel(item);
    if (!label) {
      unsectioned.push(item);
      continue;
    }
    const existing = sections.get(label);
    if (existing) {
      existing.items.push(item);
      continue;
    }
    sections.set(label, {
      label,
      order: Number.isFinite(item?.order) ? item.order : Number.MAX_SAFE_INTEGER,
      items: [item],
    });
  }

  const groups = [];
  if (unsectioned.length > 0) {
    groups.push({ label: null, items: unsectioned });
  }
  const sectioned = [...sections.values()].sort(
    (left, right) => left.order - right.order || left.label.localeCompare(right.label),
  );
  for (const section of sectioned) {
    groups.push({ label: section.label, items: section.items });
  }

  return groups.length > 0 ? groups : [{ label: null, items: [] }];
}

export default groupItemsIntoSections;
