import { SummaryStrip } from './SummaryStrip.jsx';

function readPath(source, path) {
  if (!source || !path) return undefined;
  return String(path)
    .split('.')
    .filter(Boolean)
    .reduce((acc, key) => (acc && typeof acc === 'object' ? acc[key] : undefined), source);
}

function toDisplayValue(value, format) {
  if (value === null || value === undefined) return 0;
  if (format === 'percent') {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return `${numeric.toFixed(2)}%`;
  }
  if (format === 'currency') {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return `$${numeric.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  }
  return value;
}

export function AnalyticsSummaryStrip({ metrics, definitions = [], className }) {
  const items = (Array.isArray(definitions) ? definitions : []).map((definition) => {
    const id = definition.id || definition.key || definition.label;
    const label = definition.label || definition.key || id;
    const valuePath = definition.valuePath || (definition.key ? `${definition.key}.value` : undefined);
    const detail = definition.detail;
    const format = definition.format;

    const value = toDisplayValue(readPath(metrics, valuePath), format);
    return {
      id,
      label,
      value,
      detail,
    };
  });

  return <SummaryStrip items={items} className={className} />;
}

export default AnalyticsSummaryStrip;
