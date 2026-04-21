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
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '../base/components/table.jsx';
import { Badge } from './Badge.jsx';
import { Button } from './Button.jsx';
import { Skeleton } from './Skeleton.jsx';
import { Empty } from './Skeleton.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

function CellContent({ column, value }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  switch (column.type) {
    case 'badge':
      return <Badge label={String(value)} variant="secondary" />;
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

export function DataTable({
  id,
  columns = [],
  data: initialData = [],
  selection = 'none',
  pagination = true,
  page_size = 20,
  search = true,
  actions = [],
  onAction,
  onRefresh,
  loading: initialLoading = false,
  empty,
  className,
}) {
  const [data,         setData]         = useState(initialData);
  const [loading,      setLoading]      = useState(initialLoading);
  const [searchQuery,  setSearchQuery]  = useState('');
  const [sortKey,      setSortKey]      = useState(null);
  const [sortDir,      setSortDir]      = useState('asc');
  const [page,         setPage]         = useState(1);
  const [selected,     setSelected]     = useState(new Set());

  useEffect(() => {
    setData(Array.isArray(initialData) ? initialData : []);
    setPage(1);
    setSelected(new Set());
  }, [initialData]);

  useEffect(() => {
    setLoading(initialLoading);
  }, [initialLoading]);

  // Agent-controlled refresh
  useAppEvent('ui.datatable.refresh', id, async () => {
    if (!onRefresh) return;
    setLoading(true);
    try {
      const fresh = await onRefresh();
      if (fresh) setData(fresh);
    } finally {
      setLoading(false);
    }
  });

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return data;
    const q = searchQuery.toLowerCase();
    return data.filter((row) =>
      columns.some((col) => String(row[col.key] ?? '').toLowerCase().includes(q))
    );
  }, [data, searchQuery, columns]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.ceil(sorted.length / page_size);
  const paged      = pagination ? sorted.slice((page - 1) * page_size, page * page_size) : sorted;

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
    () => sorted.filter((row, rowIndex) => selected.has(getRowKey(row, rowIndex))),
    [selected, sorted],
  );

  return (
    <div className={cn('space-y-3', className)}>
      {/* Toolbar */}
      {(search || actions.length > 0) && (
        <div className="flex items-center justify-between gap-3 flex-wrap">
          {search && (
            <input
              type="search"
              placeholder="Search…"
              value={searchQuery}
              onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
              className="h-9 w-full max-w-xs rounded-md border border-input bg-transparent px-3 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
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
                  disabled={action.requires_selection && selected.size === 0}
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
      ) : paged.length === 0 ? (
        <Empty
          title={empty?.title ?? 'No results'}
          message={empty?.message}
          action={empty?.action ? { ...empty.action, onClick: () => onAction?.(empty.action.id, []) } : undefined}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {selection !== 'none' && <TableHead className="w-10" />}
              {columns.map((col) => (
                <TableHead
                  key={col.key}
                  style={col.width ? { width: col.width } : undefined}
                  className={cn(col.sortable && 'cursor-pointer select-none hover:text-foreground')}
                  onClick={col.sortable ? () => toggleSort(col.key) : undefined}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span className="ml-1 text-xs">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((row, rowIdx) => {
              const rowKey = getRowKey(
                row,
                pagination ? ((page - 1) * page_size) + rowIdx : rowIdx,
              );
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
      )}

      {/* Pagination */}
      {pagination && totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{sorted.length} results</span>
          <div className="flex items-center gap-1">
            <Button
              label="‹"
              variant="ghost"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            />
            <span className="px-2">Page {page} of {totalPages}</span>
            <Button
              label="›"
              variant="ghost"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
