// DataTable — tabular data primitive for the ui.render event system.
//
// Payload contract:
//   title?    string
//   columns   Array<{ key: string, label: string, align?: 'left'|'right'|'center' }>
//   rows      Array<Record<string, any>>
//   caption?  string
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell, TableCaption } from '../../../ui/base';

const ALIGN = { left: 'text-left', right: 'text-right', center: 'text-center' };

export default function DataTable({ payload = {} }) {
  const { title, columns = [], rows = [], caption } = payload;

  if (!columns.length) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        No columns defined.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {title && (
        <div className="px-4 py-3 border-b border-border">
          <p className="text-sm font-semibold text-foreground">{title}</p>
        </div>
      )}
      <div className="overflow-x-auto">
        <Table>
          {caption && <TableCaption className="text-xs text-muted-foreground pb-2">{caption}</TableCaption>}
          <TableHeader>
            <TableRow className="hover:bg-transparent border-border">
              {columns.map((col) => (
                <TableHead
                  key={col.key}
                  className={`text-xs font-medium text-muted-foreground uppercase tracking-wide ${ALIGN[col.align] || 'text-left'}`}
                >
                  {col.label ?? col.key}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="text-center text-sm text-muted-foreground py-6">
                  No data.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row, i) => (
                <TableRow key={i} className="border-border hover:bg-muted/40 transition-colors">
                  {columns.map((col) => (
                    <TableCell
                      key={col.key}
                      className={`text-sm text-foreground py-2 ${ALIGN[col.align] || 'text-left'}`}
                    >
                      {row[col.key] ?? '—'}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
