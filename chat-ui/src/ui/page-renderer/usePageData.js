/**
 * usePageData — fetch and cache data for page sections with api_endpoint bindings.
 *
 * Each AppPageSection that declares an api_endpoint gets its own fetch lifecycle.
 * The PageRenderer calls this once per page; individual SectionRenderers read
 * from the returned map by section id.
 *
 * Refetch is triggered by:
 *   - Initial mount
 *   - ui.datatable.refresh  (DataTable sections)
 *   - ui.form.reset         (Form sections)
 *   - Explicit refetch(sectionId) call from SectionRenderer
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { flattenSections } from './schemaUtils.js';

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' };

async function fetchEndpoint(endpoint) {
  const res = await fetch(endpoint, { headers: DEFAULT_HEADERS });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * @param {AppPageSection[]} sections
 * @returns {{
 *   sectionData: Record<string, { data: any, loading: boolean, error: string|null }>,
 *   refetch: (sectionId: string) => Promise<void>
 * }}
 */
export function usePageData(sections = []) {
  const bindableSections = useMemo(
    () => flattenSections(sections).filter((section) => section.config?.api_endpoint),
    [sections],
  );

  const bindableSectionMap = useMemo(
    () => Object.fromEntries(bindableSections.map((section) => [section.id, section])),
    [bindableSections],
  );

  const [sectionData, setSectionData] = useState({});

  useEffect(() => {
    setSectionData((prev) => {
      if (bindableSections.length === 0) {
        return {};
      }

      const next = {};
      for (const section of bindableSections) {
        next[section.id] = prev[section.id] ?? { data: null, loading: true, error: null };
      }
      return next;
    });
  }, [bindableSections]);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const fetchSection = useCallback(async (sectionOrId) => {
    const section = typeof sectionOrId === 'string'
      ? bindableSectionMap[sectionOrId]
      : sectionOrId;
    const endpoint = section.config?.api_endpoint;
    if (!endpoint) return null;

    setSectionData((prev) => ({
      ...prev,
      [section.id]: { ...prev[section.id], loading: true, error: null },
    }));

    try {
      const data = await fetchEndpoint(endpoint);
      if (!mountedRef.current) return;
      setSectionData((prev) => ({
        ...prev,
        [section.id]: { data, loading: false, error: null },
      }));
      return data;
    } catch (err) {
      if (!mountedRef.current) return;
      setSectionData((prev) => ({
        ...prev,
        [section.id]: { data: null, loading: false, error: err.message },
      }));
      return null;
    }
  }, [bindableSectionMap]);

  // Initial fetch for all bindable sections
  useEffect(() => {
    for (const s of bindableSections) {
      fetchSection(s);
    }
  }, [bindableSections, fetchSection]);

  /**
   * Imperatively refetch a single section by id.
   * Called by SectionRenderer when a ui.* event signals a data refresh.
   */
  const refetch = useCallback(async (sectionId) => {
    if (!sectionId) {
      await Promise.all(bindableSections.map((section) => fetchSection(section)));
      return null;
    }

    return fetchSection(sectionId);
  }, [bindableSections, fetchSection]);

  return { sectionData, refetch };
}
