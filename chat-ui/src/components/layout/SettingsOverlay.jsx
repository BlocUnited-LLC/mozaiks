/**
 * SettingsOverlay — Settings side-panel overlay
 *
 * Self-contained: fetches schema and current settings when opened.
 * Renders as a fixed full-screen backdrop + right-sliding panel.
 *
 * @module @mozaiks/chat-ui/components/layout/SettingsOverlay
 */

import React, { useState, useEffect, useCallback } from 'react';
import { fetchSettingsConfig, fetchSettings, saveSettings, resetSettings } from '../../coreBridge';

// ---------------------------------------------------------------------------
// Field renderers
// ---------------------------------------------------------------------------
const TextField = ({ value, onChange }) => (
  <input
    type="text"
    value={value ?? ''}
    onChange={(e) => onChange(e.target.value)}
    className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-[rgba(var(--color-primary-light-rgb),0.2)] text-white text-sm focus:outline-none focus:border-[rgba(var(--color-primary-rgb),0.5)] transition-colors oxanium"
  />
);

const NumberField = ({ value, onChange }) => (
  <input
    type="number"
    value={value ?? ''}
    onChange={(e) => onChange(Number(e.target.value))}
    className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-[rgba(var(--color-primary-light-rgb),0.2)] text-white text-sm focus:outline-none focus:border-[rgba(var(--color-primary-rgb),0.5)] transition-colors oxanium"
  />
);

const ToggleField = ({ value, onChange }) => (
  <label className="relative inline-flex items-center cursor-pointer">
    <input
      type="checkbox"
      className="sr-only peer"
      checked={!!value}
      onChange={(e) => onChange(e.target.checked)}
    />
    <div className="w-10 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-0.5 after:start-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[var(--color-primary)]" />
  </label>
);

const SelectField = ({ value, onChange, options = [] }) => (
  <select
    value={value ?? ''}
    onChange={(e) => onChange(e.target.value)}
    className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-[rgba(var(--color-primary-light-rgb),0.2)] text-white text-sm focus:outline-none focus:border-[rgba(var(--color-primary-rgb),0.5)] transition-colors oxanium"
  >
    {options.map((o) => {
      const label = typeof o === 'string' ? o : o.label;
      const val = typeof o === 'string' ? o : o.value;
      return (
        <option key={val} value={val}>
          {label}
        </option>
      );
    })}
  </select>
);

const FIELD_RENDERERS = {
  text: TextField,
  string: TextField,
  number: NumberField,
  integer: NumberField,
  boolean: ToggleField,
  toggle: ToggleField,
  select: SelectField,
};

// ---------------------------------------------------------------------------
// SettingsGroup
// ---------------------------------------------------------------------------
const SettingsGroup = ({ plugin, fields, values, onChange, onSave, onReset, saving, dirty }) => (
  <div className="rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.12)] bg-slate-900/40 overflow-hidden mb-4">
    {/* Group header */}
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(var(--color-primary-light-rgb),0.08)] bg-slate-900/60">
      <span className="text-xs font-semibold uppercase tracking-widest text-[rgba(var(--color-primary-light-rgb),0.6)] oxanium">
        {plugin.replace(/_/g, ' ')}
      </span>
      {dirty ? (
        <div className="flex items-center gap-2">
          <button
            onClick={() => onReset(plugin)}
            disabled={saving === plugin}
            className="text-[11px] text-slate-500 hover:text-slate-300 transition-colors oxanium"
          >
            Reset
          </button>
          <button
            onClick={() => onSave(plugin)}
            disabled={saving === plugin}
            className="text-[11px] px-3 py-1 rounded-md bg-[rgba(var(--color-primary-rgb),0.2)] text-[var(--color-primary-light)] border border-[rgba(var(--color-primary-rgb),0.3)] hover:bg-[rgba(var(--color-primary-rgb),0.35)] transition-colors oxanium"
          >
            {saving === plugin ? 'Saving…' : 'Save'}
          </button>
        </div>
      ) : null}
    </div>

    {/* Fields */}
    <div className="p-4 space-y-4">
      {fields.map((field) => {
        const FieldComponent = FIELD_RENDERERS[field.type] || TextField;
        return (
          <div key={field.key}>
            <label className="block text-xs text-slate-400 mb-1.5 oxanium">
              {field.label || field.key}
              {field.required && <span className="text-red-400 ml-1">*</span>}
            </label>
            <FieldComponent
              value={values?.[plugin]?.[field.key]}
              onChange={(val) => onChange(plugin, field.key, val)}
              options={field.options}
            />
            {field.description && (
              <p className="text-[11px] text-slate-600 mt-1">{field.description}</p>
            )}
          </div>
        );
      })}
    </div>
  </div>
);

// ---------------------------------------------------------------------------
// SettingsOverlay
// ---------------------------------------------------------------------------
const SettingsOverlay = ({ isOpen, onClose }) => {
  const [schema, setSchema] = useState([]);
  const [values, setValues] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [dirty, setDirty] = useState(new Set());

  // Fetch config + values on open
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    const load = async () => {
      try {
        const [cfg, current] = await Promise.all([
          fetchSettingsConfig().catch(() => null),
          fetchSettings().catch(() => null),
        ]);
        if (cancelled) return;
        setSchema(cfg?.groups || cfg || []);
        setValues(current?.settings || current || {});
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load settings');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  // Toast auto-hide
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const showToast = (msg, type = 'success') => {
    setToast({ msg, type });
  };

  const handleChange = useCallback((plugin, key, value) => {
    setValues((prev) => ({
      ...prev,
      [plugin]: { ...(prev[plugin] || {}), [key]: value },
    }));
    setDirty((prev) => new Set([...prev, plugin]));
  }, []);

  const handleSave = useCallback(async (plugin) => {
    setSaving(plugin);
    try {
      await saveSettings(plugin, values[plugin] || {});
      setDirty((prev) => {
        const next = new Set(prev);
        next.delete(plugin);
        return next;
      });
      showToast(`${plugin.replace(/_/g, ' ')} saved`);
    } catch (err) {
      showToast(err.message || 'Save failed', 'error');
    } finally {
      setSaving(null);
    }
  }, [values]);

  const handleSaveAll = useCallback(async () => {
    setSaving('__all__');
    try {
      await Promise.all([...dirty].map((p) => saveSettings(p, values[p] || {})));
      setDirty(new Set());
      showToast('All settings saved');
    } catch (err) {
      showToast(err.message || 'Save failed', 'error');
    } finally {
      setSaving(null);
    }
  }, [dirty, values]);

  const handleReset = useCallback(async (plugin) => {
    try {
      const defaults = await resetSettings(plugin);
      setValues((prev) => ({ ...prev, [plugin]: defaults?.settings?.[plugin] || defaults?.[plugin] || {} }));
      setDirty((prev) => {
        const next = new Set(prev);
        next.delete(plugin);
        return next;
      });
      showToast(`${plugin.replace(/_/g, ' ')} reset`);
    } catch (err) {
      showToast(err.message || 'Reset failed', 'error');
    }
  }, []);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Slide-in panel (right side) */}
      <div className="relative ml-auto h-full w-full max-w-md flex flex-col bg-[rgba(5,10,24,0.98)] border-l border-[rgba(var(--color-primary-light-rgb),0.15)] shadow-[-20px_0_60px_rgba(0,0,0,0.5)]">
        {/* Panel header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[rgba(var(--color-primary-light-rgb),0.12)] flex-shrink-0">
          <div className="flex items-center gap-2.5">
            {/* Gear icon */}
            <svg className="w-4 h-4 text-[rgba(var(--color-primary-light-rgb),0.6)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <span className="text-sm font-semibold uppercase tracking-widest text-[rgba(var(--color-primary-light-rgb),0.8)] oxanium">
              Settings
            </span>
          </div>
          <div className="flex items-center gap-3">
            {dirty.size > 0 && (
              <button
                onClick={handleSaveAll}
                disabled={saving === '__all__'}
                className="text-[11px] px-3 py-1.5 rounded-lg bg-[rgba(var(--color-primary-rgb),0.2)] text-[var(--color-primary-light)] border border-[rgba(var(--color-primary-rgb),0.3)] hover:bg-[rgba(var(--color-primary-rgb),0.35)] transition-colors oxanium"
              >
                {saving === '__all__' ? 'Saving…' : `Save all (${dirty.size})`}
              </button>
            )}
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-white/10 text-slate-400 transition-colors"
              title="Close settings"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Toast */}
        {toast && (
          <div
            className={`mx-4 mt-3 px-4 py-2.5 rounded-lg text-sm oxanium flex-shrink-0 ${
              toast.type === 'error'
                ? 'bg-red-900/40 border border-red-500/30 text-red-300'
                : 'bg-green-900/40 border border-green-500/30 text-green-300'
            }`}
          >
            {toast.msg}
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--color-primary)]" />
            </div>
          ) : error ? (
            <div className="text-center py-10">
              <p className="text-red-400 text-sm oxanium">{error}</p>
            </div>
          ) : schema.length === 0 ? (
            <div className="text-center py-10">
              <p className="text-slate-500 text-sm oxanium">No configurable settings available.</p>
            </div>
          ) : (
            schema.map((group) => (
              <SettingsGroup
                key={group.plugin}
                plugin={group.plugin}
                fields={group.fields || []}
                values={values}
                onChange={handleChange}
                onSave={handleSave}
                onReset={handleReset}
                saving={saving}
                dirty={dirty.has(group.plugin)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default SettingsOverlay;
