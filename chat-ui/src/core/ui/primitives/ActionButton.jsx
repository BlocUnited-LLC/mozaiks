// ActionButton — single action or button group primitive for the ui.render event system.
//
// Payload contract:
//   title?    string
//   layout?   'row'|'column'   default: 'row'
//   actions   Array<{
//     label:    string
//     value:    string
//     variant?: string         button variant
//     disabled?: boolean
//   }>
import { useState } from 'react';
import { Button } from '../../../ui/base';

export default function ActionButton({ payload = {}, onResponse }) {
  const { title, layout = 'row', actions = [] } = payload;
  const [responded, setResponded] = useState(false);
  const [selectedValue, setSelectedValue] = useState(null);

  const handleAction = (action) => {
    if (responded || action.disabled) return;
    setResponded(true);
    setSelectedValue(action.value);
    onResponse?.({ status: 'selected', value: action.value, label: action.label });
  };

  const layoutCls = layout === 'column' ? 'flex-col' : 'flex-row flex-wrap';

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {title && <p className="text-sm font-medium text-foreground">{title}</p>}
      <div className={`flex gap-2 ${layoutCls}`}>
        {actions.map((action, i) => {
          const isSelected = selectedValue === action.value;
          const variant = isSelected
            ? 'primary'
            : (action.variant || (i === 0 ? 'primary' : 'outline'));
          return (
            <Button
              key={i}
              variant={variant}
              size="sm"
              disabled={responded && !isSelected}
              onClick={() => handleAction(action)}
              className={layout === 'column' ? 'w-full justify-center' : ''}
            >
              {action.label}
            </Button>
          );
        })}
      </div>
    </div>
  );
}
