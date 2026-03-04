/**
 * Config Validation Overlay
 *
 * Dev-mode in-browser overlay that surfaces JSON config issues as a
 * dismissible banner. Non-tech founders see exactly what's wrong and
 * how to fix it — no digging through DevTools console required.
 *
 * Only renders when there are errors or warnings. Info-level messages
 * stay in the console to avoid noise.
 *
 * @module @mozaiks/chat-ui/config/ConfigValidationOverlay
 */

import React, { useState, useEffect, useCallback } from 'react';
import { validateAllConfigs, logValidationResults } from './validateConfig';

const LEVEL_STYLES = {
  error: {
    bg: 'rgba(239,68,68,0.12)',
    border: 'rgba(239,68,68,0.4)',
    icon: '❌',
    text: '#fca5a5',
  },
  warn: {
    bg: 'rgba(234,179,8,0.10)',
    border: 'rgba(234,179,8,0.35)',
    icon: '⚠️',
    text: '#fde68a',
  },
};

export default function ConfigValidationOverlay() {
  const [issues, setIssues] = useState([]);
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    validateAllConfigs().then((results) => {
      if (cancelled) return;
      // Also log to console for dev-tools users
      logValidationResults(results);
      // Only surface errors + warnings in the overlay (skip info)
      const actionable = (results || []).filter(
        (r) => r.level === 'error' || r.level === 'warn'
      );
      setIssues(actionable);
    });
    return () => { cancelled = true; };
  }, []);

  const dismiss = useCallback(() => setDismissed(true), []);

  if (dismissed || issues.length === 0) return null;

  const errorCount = issues.filter((i) => i.level === 'error').length;
  const warnCount = issues.filter((i) => i.level === 'warn').length;

  const summaryParts = [];
  if (errorCount > 0) summaryParts.push(`${errorCount} error${errorCount > 1 ? 's' : ''}`);
  if (warnCount > 0) summaryParts.push(`${warnCount} warning${warnCount > 1 ? 's' : ''}`);
  const summary = summaryParts.join(', ');

  const bannerBg = errorCount > 0
    ? 'rgba(127,29,29,0.92)'
    : 'rgba(113,63,18,0.92)';

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 99999,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      }}
    >
      {/* Summary bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          background: bannerBg,
          backdropFilter: 'blur(12px)',
          borderBottom: `1px solid ${errorCount > 0 ? 'rgba(239,68,68,0.4)' : 'rgba(234,179,8,0.35)'}`,
          color: '#fef2f2',
          fontSize: '13px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>{errorCount > 0 ? '❌' : '⚠️'}</span>
          <span style={{ fontWeight: 600 }}>
            Config check: {summary}
          </span>
          <button
            onClick={() => setExpanded((e) => !e)}
            style={{
              background: 'rgba(255,255,255,0.12)',
              border: '1px solid rgba(255,255,255,0.2)',
              color: '#fff',
              borderRadius: '6px',
              padding: '2px 10px',
              fontSize: '11px',
              cursor: 'pointer',
              marginLeft: '8px',
            }}
          >
            {expanded ? 'Hide details' : 'Show details'}
          </button>
        </div>
        <button
          onClick={dismiss}
          style={{
            background: 'none',
            border: 'none',
            color: 'rgba(255,255,255,0.6)',
            cursor: 'pointer',
            fontSize: '18px',
            lineHeight: 1,
            padding: '0 4px',
          }}
          title="Dismiss"
        >
          ×
        </button>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div
          style={{
            maxHeight: '280px',
            overflowY: 'auto',
            background: 'rgba(15,23,42,0.96)',
            backdropFilter: 'blur(16px)',
            borderBottom: '1px solid rgba(100,116,139,0.3)',
            padding: '8px 0',
          }}
        >
          {issues.map((issue, idx) => {
            const style = LEVEL_STYLES[issue.level] || LEVEL_STYLES.warn;
            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '8px',
                  padding: '8px 16px',
                  borderLeft: `3px solid ${style.border}`,
                  margin: '4px 12px',
                  borderRadius: '4px',
                  background: style.bg,
                }}
              >
                <span style={{ flexShrink: 0 }}>{style.icon}</span>
                <div style={{ fontSize: '12px', lineHeight: '1.5' }}>
                  <span
                    style={{
                      fontWeight: 600,
                      color: 'rgba(148,163,184,0.9)',
                      marginRight: '6px',
                    }}
                  >
                    {issue.file}
                  </span>
                  <span style={{ color: style.text }}>{issue.message}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
