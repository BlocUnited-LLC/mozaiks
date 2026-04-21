/**
 * Stat primitive — single KPI metric display with optional trend indicator.
 *
 * Schema properties:
 *   id           {string}  — component id for event routing
 *   label        {string}  — metric label
 *   value        {any}     — current value (can be overridden by ui.stat.update event)
 *   trend        {number}  — trend delta (positive = up, negative = down)
 *   trend_direction {string} — "up_good" | "up_bad" | "neutral" (default: "up_good")
 *   format       {string}  — "number" | "currency" | "percentage" | "compact"
 *   icon         {ReactNode}
 *   color        {string}  — "default" | "primary" | "success" | "warning" | "danger"
 *
 * Agent event: ui.stat.update
 *   payload: { component_id, value, trend }
 */

import { useState, useEffect } from 'react';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

function formatValue(value, format) {
  if (value === null || value === undefined) return '—';
  switch (format) {
    case 'currency':
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
    case 'percentage':
      return `${Number(value).toFixed(1)}%`;
    case 'compact':
      return new Intl.NumberFormat('en-US', { notation: 'compact' }).format(value);
    default:
      return typeof value === 'number'
        ? new Intl.NumberFormat('en-US').format(value)
        : String(value);
  }
}

const colorMap = {
  default: '',
  primary: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  danger:  'text-destructive',
};

export function Stat({
  id,
  label,
  value: initialValue,
  trend: initialTrend,
  trend_direction = 'up_good',
  format,
  icon,
  color = 'default',
  className,
}) {
  const [value, setValue] = useState(initialValue);
  const [trend, setTrend]  = useState(initialTrend);

  useEffect(() => {
    setValue(initialValue);
  }, [initialValue]);

  useEffect(() => {
    setTrend(initialTrend);
  }, [initialTrend]);

  useAppEvent('ui.stat.update', id, (payload) => {
    if (payload.value !== undefined) setValue(payload.value);
    if (payload.trend !== undefined) setTrend(payload.trend);
  });

  const isUp   = trend > 0;
  const isDown = trend < 0;
  const trendPositive = trend_direction === 'up_good' ? isUp : isDown;
  const trendNegative = trend_direction === 'up_good' ? isDown : isUp;

  return (
    <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon && <span className="text-muted-foreground">{icon}</span>}
      </div>
      <p className={cn('text-2xl font-bold tracking-tight', colorMap[color])}>
        {formatValue(value, format)}
      </p>
      {trend !== null && trend !== undefined && (
        <p className={cn(
          'mt-1 text-xs font-medium',
          trendPositive && 'text-success',
          trendNegative && 'text-destructive',
          !trendPositive && !trendNegative && 'text-muted-foreground',
        )}>
          {isUp ? '↑' : isDown ? '↓' : '→'}
          {' '}
          {Math.abs(trend).toFixed(1)}%
        </p>
      )}
    </div>
  );
}
