// AlertBanner — info/warning/error/success banner primitive for the ui.render event system.
//
// Payload contract:
//   message   string
//   variant?  'info'|'success'|'warning'|'destructive'   default: 'info'
//   title?    string
//   dismissible? boolean   default: false
//   actions?  Array<{ label: string, value: string, variant?: string }>
import { useState } from 'react';
import { Button } from '../../../ui/base';

const VARIANT_STYLES = {
  info:        { wrap: 'border-primary/40 bg-primary/10',       icon: 'ℹ', iconCls: 'text-primary',             title: 'text-primary',           body: 'text-primary/80' },
  success:     { wrap: 'border-success/40 bg-success/10',       icon: '✓', iconCls: 'text-success',             title: 'text-success',           body: 'text-success/80' },
  warning:     { wrap: 'border-warning/40 bg-warning/10',       icon: '⚠', iconCls: 'text-warning',             title: 'text-warning',           body: 'text-warning/80' },
  destructive: { wrap: 'border-destructive/40 bg-destructive/10', icon: '✕', iconCls: 'text-destructive',       title: 'text-destructive',       body: 'text-destructive/80' },
};

export default function AlertBanner({ payload = {}, onResponse }) {
  const { message, variant = 'info', title, dismissible = false, actions = [] } = payload;
  const [dismissed, setDismissed] = useState(false);
  const [responded, setResponded] = useState(false);

  if (dismissed) return null;

  const s = VARIANT_STYLES[variant] || VARIANT_STYLES.info;

  const handleAction = (action) => {
    if (responded) return;
    setResponded(true);
    onResponse?.({ status: 'selected', value: action.value, label: action.label });
  };

  const handleDismiss = () => {
    setDismissed(true);
    onResponse?.({ status: 'dismissed' });
  };

  return (
    <div className={`rounded-lg border p-4 ${s.wrap}`}>
      <div className="flex items-start gap-3">
        <span className={`text-base flex-shrink-0 mt-0.5 ${s.iconCls}`}>{s.icon}</span>
        <div className="flex-1 min-w-0 space-y-1">
          {title && <p className={`text-sm font-semibold ${s.title}`}>{title}</p>}
          <p className={`text-sm leading-relaxed ${s.body}`}>{message}</p>
          {actions.length > 0 && !responded && (
            <div className="flex flex-wrap gap-2 pt-1">
              {actions.map((action, i) => (
                <Button
                  key={i}
                  variant={action.variant || (i === 0 ? 'primary' : 'outline')}
                  size="sm"
                  onClick={() => handleAction(action)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          )}
        </div>
        {dismissible && (
          <button
            onClick={handleDismiss}
            className={`flex-shrink-0 text-sm leading-none hover:opacity-70 transition-opacity ${s.iconCls}`}
            aria-label="Dismiss"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
