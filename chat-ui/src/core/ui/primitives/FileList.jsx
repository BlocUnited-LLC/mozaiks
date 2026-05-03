// FileList — file listing with optional download actions primitive for the ui.render event system.
//
// Payload contract:
//   title?  string
//   files   Array<{
//     name:      string
//     size?:     string   e.g. "42 KB"
//     type?:     string   e.g. "Python", "YAML", "PDF"
//     url?:      string   download/view URL
//     status?:   'ready'|'generating'|'error'
//   }>
const STATUS_STYLES = {
  ready:      { dot: 'bg-success', label: 'text-muted-foreground' },
  generating: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  error:      { dot: 'bg-destructive', label: 'text-destructive' },
};

const FILE_ICON = {
  python:     '🐍',
  py:         '🐍',
  javascript: '📜',
  js:         '📜',
  typescript: '📘',
  ts:         '📘',
  yaml:       '⚙',
  yml:        '⚙',
  json:       '{}',
  pdf:        '📄',
  markdown:   '📝',
  md:         '📝',
  html:       '🌐',
  css:        '🎨',
};

function getIcon(name, type) {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const t = type?.toLowerCase() || '';
  return FILE_ICON[t] || FILE_ICON[ext] || '📁';
}

export default function FileList({ payload = {}, onResponse }) {
  const { title, files = [] } = payload;

  const handleDownload = (file) => {
    onResponse?.({ status: 'download', name: file.name, url: file.url });
  };

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {title && (
        <div className="px-4 py-3 border-b border-border">
          <p className="text-sm font-semibold text-foreground">{title}</p>
        </div>
      )}
      <ul className="divide-y divide-border">
        {files.length === 0 && (
          <li className="px-4 py-6 text-sm text-center text-muted-foreground">No files.</li>
        )}
        {files.map((file, i) => {
          const s = STATUS_STYLES[file.status || 'ready'] || STATUS_STYLES.ready;
          return (
            <li key={i} className="flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors">
              <span className="text-xl flex-shrink-0" aria-hidden="true">{getIcon(file.name, file.type)}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{file.name}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  {file.type && <span className="text-xs text-muted-foreground">{file.type}</span>}
                  {file.size && <span className="text-xs text-muted-foreground">{file.size}</span>}
                  {file.status && file.status !== 'ready' && (
                    <span className={`text-xs ${s.label}`}>{file.status}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                {file.url && file.status !== 'generating' && (
                  <a
                    href={file.url}
                    download={file.name}
                    onClick={() => handleDownload(file)}
                    className="text-xs text-primary hover:underline"
                  >
                    Download
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
