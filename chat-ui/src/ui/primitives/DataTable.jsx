/**
 * DataTable primitive — sortable, filterable data table with agent-controlled refresh.
 *
 * Schema properties:
 *   id         {string}   — component id for event routing
 *   columns    {Column[]} — column definitions
 *   data       {object[]} — initial data rows (can be provided directly by page renderer)
 *   selection  {string}   — "none" | "single" | "multi"
 *   pagination {boolean}  — show pagination controls (default true)
 *   page_size  {number}   — rows per page (default 20)
 *   search     {boolean}  — show search input (default true)
 *   actions    {Action[]} — toolbar buttons above the table
 *   onAction   {Function} — (actionId, selectedRows) => void
 *   empty      {object}   — { title, message, action } when no data
 *
 * Column schema:
 *   key       {string}
 *   label     {string}
 *   type      {string}   — "text" | "number" | "date" | "badge" | "actions"
 *   sortable  {boolean}
 *   width     {string}
 *
 * Agent event: ui.datatable.refresh
 *   payload: { component_id }
 *   — signals the table to re-request its data from the page renderer's data context
 *   — primitives fire an onRefresh callback; page renderer re-fetches the data_source
 */

import { useState, useMemo, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '../base/components/table.jsx';
import { StatusPill } from './Surface.jsx';
import { Button } from './Button.jsx';
import { Skeleton } from './Skeleton.jsx';
import { Empty } from './Skeleton.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const EMPTY_ARRAY = Object.freeze([]);

function statusTone(value) {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (['active', 'approved', 'complete', 'completed', 'connected', 'hosted', 'live', 'paid', 'ready', 'success'].includes(normalized)) {
    return 'success';
  }
  if (['pending', 'requested', 'submitted', 'review', 'in_review', 'queued', 'draft'].includes(normalized)) {
    return 'warning';
  }
  if (['blocked', 'cancelled', 'canceled', 'denied', 'error', 'failed', 'rejected'].includes(normalized)) {
    return 'destructive';
  }
  return 'default';
}

function CellContent({ column, value }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  switch (column.type) {
    case 'status':
      return <StatusPill label={String(value)} tone={statusTone(value)} />;
    case 'badge':
      return <StatusPill label={String(value)} tone="default" />;
    case 'date':
      return <span>{new Date(value).toLocaleDateString()}</span>;
    case 'number':
      return <span className="tabular-nums">{Number(value).toLocaleString()}</span>;
    default:
      return <span>{String(value)}</span>;
  }
}

function getRowKey(row, rowIndex) {
  if (row?.id !== undefined && row?.id !== null) return `id:${row.id}`;
  if (row?.key !== undefined && row?.key !== null) return `key:${row.key}`;
  return `row:${rowIndex}`;
}

function MobileRowCard({
  columns,
  row,
  rowKey,
  selection,
  isSelected,
  onToggle,
}) {
  return (
    <article
      className={cn(
        'min-w-0 rounded border border-border bg-card space-y-3 p-4 md:hidden',
        selection !== 'none' && 'cursor-pointer',
        isSelected && 'ring-2 ring-primary/25',
      )}
      onClick={selection !== 'none' ? () => onToggle(rowKey) : undefined}
    >
      {selection !== 'none' && (
        <div className="flex items-center justify-between gap-3 border-b border-border/32 pb-3">
          <div>
            <div className="text-sm font-medium text-foreground">{isSelected ? 'Selected' : 'Select record'}</div>
          </div>
          <input
            type={selection === 'multi' ? 'checkbox' : 'radio'}
            aria-label="Select record"
            checked={isSelected}
            onChange={() => onToggle(rowKey)}
            onClick={(event) => event.stopPropagation()}
            className="h-4 w-4"
          />
        </div>
      )}

      {columns.map((col) => (
        <div key={col.key} className="space-y-1.5">
          <div className="text-xs font-semibold text-muted-foreground">
            {col.label}
          </div>
          <div className="break-words [overflow-wrap:anywhere] text-sm text-foreground">
            <CellContent column={col} value={row[col.key]} />
          </div>
        </div>
      ))}
    </article>
  );
}

export function DataTable({
  id,
  columns = EMPTY_ARRAY,
  data: initialData = EMPTY_ARRAY,
  selection = 'none',
  pagination = true,
  pagination_mode = 'client',
  page_size = 20,
  total,
  query,
  onQueryChange,
  search = true,
  search_placeholder = 'Search...',
  search_keys,
  actions = EMPTY_ARRAY,
  onAction,
  onRefresh,
  loading: initialLoading = false,
  error = null,
  empty,
  className,
}) {
  const server = pagination_mode === 'server';
  const [clientData,   setData]         = useState(initialData);
  const [clientLoading, setLoading]    = useState(initialLoading);
  const [clientSearch, setSearchQuery] = useState('');
  const [sortKey,      setSortKey]      = useState(null);
  const [sortDir,      setSortDir]      = useState('asc');
  const [clientPage,   setPage]         = useState(1);
  const [selected,     setSelected]     = useState(new Set());
  const data = server ? initialData : clientData;
  const loading = server ? initialLoading : clientLoading;
  const page = server ? (query?.page ?? 1) : clientPage;
  const searchQuery = server ? (query?.search ?? '') : clientSearch;

  useEffect(() => {
    if (!server) {
      setData(Array.isArray(initialData) ? initialData : []);
      setPage(1);
    }
    setSelected(new Set());
  }, [initialData, server, query?.page, query?.search]);

  useEffect(() => {
    setLoading(initialLoading);
  }, [initialLoading]);

  // Agent-controlled refresh
  useAppEvent('ui.datatable.refresh', id, async () => {
    if (!onRefresh) return;
    if (server) {
      await onRefresh();
      return;
    }
    setLoading(true);
    try {
      const fresh = await onRefresh();
      if (fresh) setData(fresh);
    } finally {
      setLoading(false);
    }
  });

  const filtered = useMemo(() => {
    if (server || !searchQuery.trim()) return data;
    const q = searchQuery.toLowerCase();
    const keys = search_keys ?? columns.map((column) => column.key);
    return data.filter((row) =>
      keys.some((key) => String(row[key] ?? '').toLowerCase().includes(q))
    );
  }, [data, searchQuery, columns, search_keys, server]);

  // Row identity must survive sorting, filtering, and pagination.
  const rowKeys = useMemo(() => new Map(data.map((row, index) => [row, getRowKey(row, index)])), [data]);

  const sorted = useMemo(() => {
    if (server || !sortKey) return filtered;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [filtered, sortKey, sortDir, server]);

  const resultCount = server ? total : sorted.length;
  const totalPages = Math.max(1, Math.ceil((resultCount ?? 0) / page_size));
  const paged = !server && pagination ? sorted.slice((page - 1) * page_size, page * page_size) : sorted;
  const changePage = (nextPage) => {
    setSelected(new Set());
    if (server) onQueryChange?.({ page: nextPage });
    else setPage(nextPage);
  };

  const toggleSort = useCallback((key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  }, [sortKey]);

  const toggleRow = useCallback((rowKey) => {
    if (selection === 'none') return;
    setSelected((prev) => {
      const next = new Set(selection === 'multi' ? prev : []);
      if (prev.has(rowKey)) next.delete(rowKey); else next.add(rowKey);
      return next;
    });
  }, [selection]);

  const selectedRows = useMemo(
    () => sorted.filter((row) => selected.has(rowKeys.get(row))),
    [selected, sorted, rowKeys],
  );

  return (
    <div className={cn('space-y-3', className)}>
      {/* Toolbar */}
      {(search || actions.length > 0) && (
        <div className="flex items-center justify-between gap-3 flex-wrap">
          {search && (
            <input
              type="search"
              placeholder={search_placeholder}
              aria-label={search_placeholder}
              value={searchQuery}
              onChange={(e) => {
                setSelected(new Set());
                if (server) onQueryChange?.({ search: e.target.value });
                else { setSearchQuery(e.target.value); setPage(1); }
              }}
              className="h-11 w-full max-w-md rounded-[var(--shell-control-radius,1rem)] border border-border/48 bg-card/34 px-4 text-sm text-foreground shadow-sm shadow-black/5 outline-none transition placeholder:text-muted-foreground/68 hover:border-border/70 focus:border-primary/42 focus:ring-2 focus:ring-primary/16"
            />
          )}
          {actions.length > 0 && (
            <div className="flex items-center gap-2 ml-auto">
              {actions.map((action) => (
                <Button
                  key={action.id}
                  label={action.label}
                  variant={action.variant ?? 'secondary'}
                  size="sm"
                  disabled={action.requires_selection && (selected.size === 0 || loading || !!error)}
                  onClick={() => onAction?.(action.id, selectedRows)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Table */}
      {loading ? (
        <Skeleton rows={5} height="h-10" />
      ) : error ? (
        <div role="alert">
          <Empty
            title={empty?.error_title || 'Unable to load records'}
            message={empty?.error_message || String(error)}
            action={onRefresh ? { label: empty?.retry_label || 'Retry', onClick: onRefresh } : undefined}
          />
        </div>
      ) : paged.length === 0 ? (
        <Empty
          title={empty?.title ?? 'No results'}
          message={empty?.message}
          actionAlign="start"
          action={empty?.action ? { ...empty.action, onClick: () => onAction?.(empty.action.id, []) } : undefined}
        />
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {paged.map((row) => {
              const rowKey = rowKeys.get(row);
              return (
                <MobileRowCard
                  key={rowKey}
                  columns={columns}
                  row={row}
                  rowKey={rowKey}
                  selection={selection}
                  isSelected={selected.has(rowKey)}
                  onToggle={toggleRow}
                />
              );
            })}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  {selection !== 'none' && <TableHead className="w-10" />}
                  {columns.map((col) => (
                    <TableHead
                      key={col.key}
                      style={col.width ? { width: col.width } : undefined}
                      className={cn(!server && col.sortable && 'cursor-pointer select-none hover:text-foreground')}
                      onClick={!server && col.sortable ? () => toggleSort(col.key) : undefined}
                    >
                      {col.label}
                      {!server && sortKey === col.key && (
                        <span className="ml-1 text-xs">{sortDir === 'asc' ? '↑' : '↓'}</span>
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {paged.map((row) => {
                  const rowKey = rowKeys.get(row);
                  const isSelected = selected.has(rowKey);
                  return (
                    <TableRow
                      key={rowKey}
                      data-state={isSelected ? 'selected' : undefined}
                      className={cn(selection !== 'none' && 'cursor-pointer')}
                      onClick={selection !== 'none' ? () => toggleRow(rowKey) : undefined}
                    >
                      {selection !== 'none' && (
                        <TableCell>
                          <input
                            type={selection === 'multi' ? 'checkbox' : 'radio'}
                            checked={isSelected}
                            onChange={() => toggleRow(rowKey)}
                            onClick={(e) => e.stopPropagation()}
                            className="h-4 w-4"
                          />
                        </TableCell>
                      )}
                      {columns.map((col) => (
                        <TableCell key={col.key}>
                          <CellContent column={col} value={row[col.key]} />
                        </TableCell>
                      ))}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </>
      )}

      {/* Pagination */}
      {pagination && (server ? Number.isSafeInteger(total) && !loading && !error : totalPages > 1) && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>{resultCount} results</span>
          <div className="flex items-center gap-1">
            <Button
              icon={<ChevronLeft size={16} />}
              aria-label="Previous page"
              title="Previous page"
              variant="ghost"
              size="sm"
              disabled={loading || page <= 1}
              onClick={() => changePage(page - 1)}
            />
            <span className="px-2" aria-live="polite">Page {page} of {totalPages}</span>
            <Button
              icon={<ChevronRight size={16} />}
              aria-label="Next page"
              title="Next page"
              variant="ghost"
              size="sm"
              disabled={loading || page >= totalPages}
              onClick={() => changePage(page + 1)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
