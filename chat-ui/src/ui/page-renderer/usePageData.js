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
import { flattenSections, resolvePath } from './schemaUtils.js';
import { authFetch } from '../../adapters/api.js';

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' };
const SEARCH_DEBOUNCE_MS = 250;

async function fetchEndpoint(endpoint, signal) {
  const res = await authFetch(endpoint, { headers: DEFAULT_HEADERS, signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function validateServerConfig(section) {
  const config = section.config;
  const keyPath = /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/;
  if (section.primitive !== 'DataTable' || config.pagination !== true
      || !Number.isInteger(config.page_size) || config.page_size < 1 || config.page_size > 100
      || !/^\/api\/modules\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(config.api_endpoint ?? '')
      || typeof config.data_key !== 'string' || !keyPath.test(config.data_key)
      || typeof config.total_key !== 'string' || !keyPath.test(config.total_key)) {
    throw new Error('Invalid server pagination contract. Use DataTable with an explicit module, row key, total key and page size (1-100).');
  }
}

/**
 * @param {AppPageSection[]} sections
 * @returns {{
 *   sectionData: Record<string, { data: any, loading: boolean, error: string|null }>,
 *   refetch: (sectionId: string, query?: {page?: number, search?: string}) => Promise<any>
 * }}
 */
export function usePageData(sections = []) {
  const bindableSections = useMemo(
    () => flattenSections(sections).filter((section) => section.config?.api_endpoint || section.config?.pagination_mode === 'server'),
    [sections],
  );

  const bindableSectionMap = useMemo(
    () => Object.fromEntries(bindableSections.map((section) => [section.id, section])),
    [bindableSections],
  );

  const [sectionData, setSectionData] = useState({});
  const requests = useRef(new Map());

  const fetchSection = useCallback(async (sectionOrId, queryUpdate) => {
    const section = typeof sectionOrId === 'string'
      ? bindableSectionMap[sectionOrId]
      : sectionOrId;
    if (!section) return null;
    const config = section.config;
    const server = config.pagination_mode === 'server';
    const previous = requests.current.get(section.id);
    previous?.controller.abort();
    const priorQuery = previous?.query ?? { page: 1, search: '' };
    const query = { ...priorQuery, ...queryUpdate };
    const searchChanged = query.search !== priorQuery.search;
    if (searchChanged) query.page = 1;
    const request = { controller: new AbortController(), query };
    requests.current.set(section.id, request);
    const isCurrent = () => requests.current.get(section.id) === request && !request.controller.signal.aborted;

    setSectionData((prev) => ({
      ...prev,
      [section.id]: { ...prev[section.id], ...(server ? { data: null, rows: [], total: null, query: { ...query } } : {}), loading: true, error: null },
    }));

    try {
      if (server) validateServerConfig(section);
      if (!Number.isSafeInteger(query.page) || query.page < 1 || typeof query.search !== 'string') {
        throw new Error('Invalid page query.');
      }
      if (server && searchChanged) {
        await new Promise(resolve => {
          const signal = request.controller.signal;
          const finish = () => {
            clearTimeout(timer);
            signal.removeEventListener('abort', finish);
            resolve();
          };
          const timer = setTimeout(finish, SEARCH_DEBOUNCE_MS);
          signal.addEventListener('abort', finish, { once: true });
        });
        if (!isCurrent()) return null;
      }
      // A deletion may invalidate the final page. Clamp once and refetch; never
      // accept rows from the old page under a different page number.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const endpoint = server
          ? `${config.api_endpoint}?${new URLSearchParams({ page: query.page, page_size: config.page_size, search: query.search })}`
          : config.api_endpoint;
        const data = await fetchEndpoint(endpoint, request.controller.signal);
        if (!isCurrent()) return null;
        let total;
        let rows;
        if (server) {
          total = resolvePath(data, config.total_key);
          rows = resolvePath(data, config.data_key);
          if (!Number.isSafeInteger(total) || total < 0 || !Array.isArray(rows)
              || rows.some(row => row === null || typeof row !== 'object' || Array.isArray(row))) {
            throw new Error('Invalid server page: expected rows and a nonnegative integer total.');
          }
          const lastPage = Math.max(1, Math.ceil(total / config.page_size));
          if (query.page > lastPage && attempt === 0) {
            query.page = lastPage;
            setSectionData(prev => ({ ...prev, [section.id]: { ...prev[section.id], query: { ...query } } }));
            continue;
          }
          const expected = Math.min(config.page_size, Math.max(0, total - (query.page - 1) * config.page_size));
          if (query.page > lastPage || rows.length !== expected) {
            throw new Error('Invalid server page: rows do not match the requested page and total.');
          }
        }
        setSectionData(prev => ({ ...prev, [section.id]: { data, rows, total, query: { ...query }, loading: false, error: null } }));
        return data;
      }
    } catch (err) {
      if (!isCurrent()) return null;
      setSectionData((prev) => ({
        ...prev,
        [section.id]: { data: null, rows: [], total: null, query: { ...query }, loading: false, error: err.message },
      }));
      return null;
    }
  }, [bindableSectionMap]);

  // Initial fetch for all bindable sections
  useEffect(() => {
    setSectionData({});
    for (const s of bindableSections) {
      fetchSection(s);
    }
    return () => {
      for (const request of requests.current.values()) request.controller.abort();
      requests.current.clear();
    };
  }, [bindableSections, fetchSection]);

  /**
   * Imperatively refetch a single section by id.
   * Called by SectionRenderer when a ui.* event signals a data refresh.
   */
  const refetch = useCallback(async (sectionId, query) => {
    if (!sectionId) {
      await Promise.all(bindableSections.map((section) => fetchSection(section)));
      return null;
    }

    return fetchSection(sectionId, query);
  }, [bindableSections, fetchSection]);

  return { sectionData, refetch };
}
