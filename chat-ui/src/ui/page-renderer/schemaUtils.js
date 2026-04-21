function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function slugSegment(value) {
  return String(value ?? 'section')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'section';
}

export function normalizeSectionNode(section, parentId = 'page', index = 0) {
  const rawSection = isRecord(section) ? section : {};
  const primitive = typeof rawSection.primitive === 'string' ? rawSection.primitive : 'Unknown';
  const id = typeof rawSection.id === 'string' && rawSection.id.trim()
    ? rawSection.id.trim()
    : `${slugSegment(parentId)}-${slugSegment(primitive)}-${index + 1}`;

  return {
    ...rawSection,
    id,
    primitive,
    config: isRecord(rawSection.config) ? rawSection.config : {},
  };
}

export function normalizeSections(sections = []) {
  if (!Array.isArray(sections)) return [];
  return sections.map((section, index) => normalizeSectionNode(section, 'page', index));
}

export function getChildSections(section) {
  const normalized = normalizeSectionNode(section);
  const children = Array.isArray(normalized.config.children) ? normalized.config.children : [];
  return children.map((child, index) => normalizeSectionNode(child, normalized.id, index));
}

export function flattenSections(sections = []) {
  const flattened = [];

  function visit(section) {
    const normalized = normalizeSectionNode(section);
    flattened.push(normalized);
    getChildSections(normalized).forEach(visit);
  }

  normalizeSections(sections).forEach(visit);
  return flattened;
}