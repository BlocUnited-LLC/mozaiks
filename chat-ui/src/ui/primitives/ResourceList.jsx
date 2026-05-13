import { cn } from '../lib/cn.js';
import { InlineEmptyState } from './Surface.jsx';

function normalizeColumns(columns) {
  return Array.isArray(columns)
    ? columns.filter((column) => column && typeof column === 'object' && column.id && column.header)
    : [];
}

function defaultItemId(item, index) {
  return item?.id ?? item?.key ?? item?.name ?? `item-${index}`;
}

function renderCell(column, item, index) {
  if (typeof column.render === 'function') return column.render(item, index);
  return item?.[column.id] ?? null;
}

function DefaultMobileItem({ item, columns, index }) {
  return (
    <article className="rounded-[1.35rem] border border-border/70 bg-card/55 p-4 shadow-sm">
      <div className="space-y-3">
        {columns.map((column) => (
          <div key={column.id} className="space-y-1">
            <div className="text-[12px] font-medium text-muted-foreground">{column.header}</div>
            <div className="text-sm text-foreground">{renderCell(column, item, index)}</div>
          </div>
        ))}
      </div>
    </article>
  );
}

export function ResourceList({
  items = [],
  columns = [],
  getItemId = defaultItemId,
  renderMobileItem,
  empty = null,
  onRowClick,
  className = '',
  tableClassName = '',
}) {
  const normalizedItems = Array.isArray(items) ? items : [];
  const normalizedColumns = normalizeColumns(columns);

  if (normalizedItems.length === 0) {
    if (!empty) return null;
    return (
      <InlineEmptyState
        title={empty.title}
        description={empty.description ?? empty.message}
        action={empty.action}
        className={className}
      />
    );
  }

  return (
    <div className={cn('overflow-hidden rounded-[1.5rem] border border-border/65 bg-background/34', className)}>
      <div className="grid gap-3 p-3 md:hidden">
        {normalizedItems.map((item, index) => (
          <div key={getItemId(item, index)}>
            {typeof renderMobileItem === 'function'
              ? renderMobileItem(item, index)
              : <DefaultMobileItem item={item} columns={normalizedColumns} index={index} />}
          </div>
        ))}
      </div>

      <div className="hidden overflow-x-auto md:block">
        <table className={cn('min-w-full table-fixed text-sm', tableClassName)}>
          <thead className="border-b border-border/65 text-left text-[12px] font-medium text-muted-foreground">
            <tr>
              {normalizedColumns.map((column) => (
                <th
                  key={column.id}
                  className={cn('px-4 py-3 font-medium', column.headerClassName)}
                  style={column.width ? { width: column.width } : undefined}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/58">
            {normalizedItems.map((item, index) => {
              const rowId = getItemId(item, index);
              const clickable = typeof onRowClick === 'function';
              return (
                <tr
                  key={rowId}
                  tabIndex={clickable ? 0 : undefined}
                  onClick={clickable ? () => onRowClick(item, index) : undefined}
                  onKeyDown={clickable ? (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onRowClick(item, index);
                    }
                  } : undefined}
                  className={cn(
                    'align-middle transition',
                    clickable && 'cursor-pointer hover:bg-card/42 focus-visible:bg-card/42 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/24',
                  )}
                >
                  {normalizedColumns.map((column) => (
                    <td key={column.id} className={cn('px-4 py-4', column.cellClassName)}>
                      {renderCell(column, item, index)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default ResourceList;
