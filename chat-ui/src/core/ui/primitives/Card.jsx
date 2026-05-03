// Card — content card primitive for the ui.render event system.
//
// Payload contract:
//   title?    string
//   subtitle? string
//   body?     string  (plain text or markdown)
//   badge?    string
//   variant?  'default'|'success'|'warning'|'destructive'
//   actions?  Array<{ label: string, value: string, variant?: string }>
import { useState } from 'react';
import { Button, Badge } from '../../../ui/base';

const VARIANT_STYLES = {
  default:     'border-border bg-card',
  success:     'border-success/40 bg-success/10',
  warning:     'border-warning/40 bg-warning/10',
  destructive: 'border-destructive/40 bg-destructive/10',
};
const BADGE_VARIANT_STYLES = {
  default:     'bg-secondary/20 text-secondary-foreground border-secondary/30',
  success:     'bg-success/20 text-success border-success/30',
  warning:     'bg-warning/20 text-warning border-warning/30',
  destructive: 'bg-destructive/20 text-destructive border-destructive/30',
};

export default function Card({ payload = {}, onResponse }) {
  const { title, subtitle, body, badge, variant = 'default', actions = [] } = payload;
  const [responded, setResponded] = useState(false);

  const handleAction = (action) => {
    if (responded) return;
    setResponded(true);
    onResponse?.({ status: 'selected', value: action.value, label: action.label });
  };

  return (
    <div className={`rounded-lg border p-4 space-y-3 ${VARIANT_STYLES[variant] || VARIANT_STYLES.default}`}>
      {(title || badge) && (
        <div className="flex items-start justify-between gap-2">
          <div>
            {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
            {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
          </div>
          {badge && (
            <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${BADGE_VARIANT_STYLES[variant] || BADGE_VARIANT_STYLES.default}`}>
              {badge}
            </span>
          )}
        </div>
      )}
      {body && <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{body}</p>}
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
  );
}
